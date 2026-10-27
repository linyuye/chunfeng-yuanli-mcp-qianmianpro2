"""Local TF-PTPRO web controller. Python stdlib HTTP + Bleak; no cloud services."""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
import contextlib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import threading
import time
import tempfile
from urllib.parse import urlsplit

from bleak import BleakClient, BleakScanner

NAME = "TF-PTPRO"
SERVICE = "0000ffac-0000-1000-8000-00805f9b34fb"
TX = "0000ffb5-0000-1000-8000-00805f9b34fb"
ACTION = "0000ffb7-0000-1000-8000-00805f9b34fb"
NOTIFY = "0000ffb8-0000-1000-8000-00805f9b34fb"
STATIC = Path(__file__).resolve().parent / "static"


def frame(message_id: int, payload: bytes) -> bytes:
    return bytes((message_id, 2, 0, len(payload) + 1)) + payload + bytes((-sum(payload) & 255,))


class Controller:
    def __init__(self):
        self.client = None
        self.gate = asyncio.Lock()
        self.devices = {}
        self.logs = deque(maxlen=120)
        self.notifications = deque(maxlen=40)
        self.notification_count = 0
        self.sequence = 0
        self.message_id = 0
        self.scanning = False
        self.connecting = False
        self.address = None
        self.device_name = None
        self.protocol_match = False
        self.heartbeat = None
        self.last_ui = time.monotonic()
        self.last_suction = 0.0
        self.waiters = []
        self.add_log("info", "控制台已就绪，请扫描并连接设备。")

    def add_log(self, kind, message, detail=None):
        self.sequence += 1
        self.logs.append({"id": self.sequence, "time": datetime.now().astimezone().strftime("%H:%M:%S"),
                          "kind": kind, "message": message, "detail": detail})

    @property
    def connected(self):
        return self.client is not None and bool(self.client.is_connected)

    def packet(self, payload):
        self.message_id = self.message_id % 15 + 1
        return frame(self.message_id, bytes(payload))

    def lost(self, client):
        if client is not self.client:
            return
        self.add_log("error", "蓝牙连接已断开。重新连接后才能操作。")

    def notification(self, _, raw):
        data = bytes(raw)
        self.notification_count += 1
        self.notifications.append({"time": datetime.now().astimezone().strftime("%H:%M:%S"),
                                   "data": data.hex(" ")})
        if len(data) >= 7 and data[1] == 7:
            for code, value, future in list(self.waiters):
                if data[4:6] == bytes((code, value)) and not future.done():
                    future.set_result(data.hex(" "))

    async def snapshot(self, touch=False):
        if touch:
            self.last_ui = time.monotonic()
        return {"connected": self.connected, "connecting": self.connecting, "scanning": self.scanning,
                "device_name": self.device_name, "address": self.address,
                "protocol_match": self.protocol_match,
                "available_actions": ["suction", "vibration"] if self.protocol_match else [],
                "devices": list(self.devices.values()),
                "logs": list(self.logs), "notifications": list(self.notifications),
                "notification_count": self.notification_count}

    async def scan(self):
        if self.scanning or self.connecting:
            raise ValueError("设备操作正在进行，请稍等。")
        async with self.gate:
            if self.connected:
                raise ValueError("已连接设备；断开后可重新扫描。")
            self.scanning = True
            self.add_log("info", "扫描附近的蓝牙 LE 设备…")
            try:
                found = await BleakScanner.discover(timeout=8, return_adv=True)
                self.devices = {}
                for device, adv in found.values():
                    services = sorted(set(str(uuid).lower() for uuid in (adv.service_uuids or [])))
                    name = (device.name or adv.local_name or "").strip() or "未命名 BLE 设备"
                    self.devices[device.address] = {
                        "address": device.address,
                        "name": name,
                        "rssi": adv.rssi,
                        "service_uuids": services,
                        # This device omits FFAC from advertisements; its stable
                        # public name is a hint only, and GATT is checked on connect.
                        "protocol_candidate": SERVICE in services or name.casefold() == NAME.casefold(),
                    }
                if self.devices:
                    candidates = sum(1 for item in self.devices.values() if item["protocol_candidate"])
                    self.add_log("success", f"发现 {len(self.devices)} 台 BLE 设备，其中 {candidates} 台广播了 FFAC 服务。")
                else:
                    self.add_log("info", "未发现设备，请确认开机、靠近电脑，并关闭手机 App 的连接。")
            finally:
                self.scanning = False
        return await self.snapshot()

    async def connect(self, address=None):
        if self.scanning or self.connecting:
            raise ValueError("设备操作正在进行，请稍等。")
        async with self.gate:
            if self.connected:
                return await self.snapshot()
            self.connecting = True
            self.add_log("info", "正在连接所选蓝牙设备并检查 GATT 协议…", address)
            client = None
            scanner = None
            try:
                if not address:
                    raise ValueError("请先扫描并明确选择一台设备。")
                if address not in self.devices:
                    raise ValueError("设备不在本次扫描列表中，请先重新扫描并选择。")
                discovered = asyncio.get_running_loop().create_future()
                def detect(device, adv):
                    if device.address == address and not discovered.done():
                        discovered.set_result(device)
                # Keep BlueZ discovery alive until the GATT connection is established;
                # its unconnected device object can disappear when discovery stops.
                scanner = BleakScanner(detection_callback=detect)
                await scanner.start()
                try:
                    device = await asyncio.wait_for(discovered, timeout=12)
                except asyncio.TimeoutError:
                    raise ValueError("找不到所选设备；请重新扫描并确认设备仍在广播。") from None
                client = BleakClient(device, disconnected_callback=self.lost, timeout=20)
                await client.connect()
                await scanner.stop()
                scanner = None
                services = {str(item.uuid).lower() for item in client.services}
                missing = [uuid for uuid in (TX, ACTION, NOTIFY) if client.services.get_characteristic(uuid) is None]
                if SERVICE not in services or missing:
                    visible = ", ".join(sorted(services)) or "无"
                    raise ValueError(f"已连接，但未匹配 TF-PTPRO 控制协议（需 FFAC 服务及 FFB5/FFB7/FFB8 特征）；设备服务：{visible}。未发送动作指令。")
                self.client = client
                self.address = device.address
                self.device_name = self.devices[address]["name"]
                self.protocol_match = True
                self.message_id = 0
                await client.start_notify(NOTIFY, self.notification)
                for _ in range(2):
                    await client.write_gatt_char(TX, self.packet((0xEF, 1)), response=True)
                    await asyncio.sleep(0.05)
                self.last_ui = time.monotonic()
                if self.heartbeat:
                    self.heartbeat.cancel()
                self.heartbeat = asyncio.create_task(self.keepalive())
                self.add_log("success", f"协议匹配成功：{self.device_name}。", device.address)
            except (Exception, asyncio.CancelledError):
                if client:
                    with contextlib.suppress(Exception):
                        await client.disconnect()
                self.client = None
                self.address = None
                self.device_name = None
                self.protocol_match = False
                raise
            finally:
                if scanner:
                    with contextlib.suppress(Exception):
                        await scanner.stop()
                self.connecting = False
        return await self.snapshot()

    async def stop_locked(self, reason="停止指令已发送。"):
        if not self.connected:
            raise ValueError("设备未连接，无法发送停止指令。")
        # Exact TF-PTPRO pause payload captured from @tryjoy/dokidoki 1.0.5.
        data = self.packet((7, 0, 0x10, 0, 9, 0, 0x12, 0))
        await self.client.write_gatt_char(ACTION, data, response=False)
        self.add_log("success", reason, data.hex(" "))

    async def stop(self):
        async with self.gate:
            await self.stop_locked()
        return await self.snapshot()

    async def stop_vibration(self):
        if not self.connected:
            raise ValueError("设备未连接，无法发送停止指令。")
        async with self.gate:
            # Per-channel stop frame; keep the suction channel untouched.
            data = self.packet((0x07, 0x00, 0x09, 0x00))
            await self.client.write_gatt_char(ACTION, data, response=False)
            self.add_log("success", "仅震动输出已停止。", data.hex(" "))
        return await self.snapshot()

    async def disconnect(self):
        async with self.gate:
            if self.connected:
                try:
                    await self.stop_locked("断开前已发送停止指令。")
                except Exception as exc:
                    self.add_log("error", "断开前停止指令发送失败，仍将断开蓝牙。", str(exc))
            if self.heartbeat and self.heartbeat is not asyncio.current_task():
                self.heartbeat.cancel()
            self.heartbeat = None
            client, self.client = self.client, None
            if client:
                await client.disconnect()
            self.address = None
            self.device_name = None
            self.protocol_match = False
            self.add_log("info", "已断开设备。")
        return await self.snapshot()

    async def keepalive(self):
        try:
            while self.connected:
                await asyncio.sleep(2)
                silent = time.monotonic() - self.last_ui
                if silent > 60:
                    await self.disconnect()
                    return
                async with self.gate:
                    if self.connected:
                        await self.client.write_gatt_char(TX, self.packet((0xEF, 1)), response=True)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.add_log("error", "蓝牙心跳失败，请重新连接。", str(exc))
            with contextlib.suppress(Exception):
                await self.disconnect()

    @staticmethod
    def level(value):
        if type(value) is not int or not 1 <= value <= 100:
            raise ValueError("强度必须是 1–100 的整数。")
        return value

    async def action(self, kind, level, duration=1):
        if kind not in ("suction", "vibration"):
            raise ValueError("仅开放吮吸和限时震动测试。")
        level = self.level(level)
        if kind == "vibration" and duration is not None and (type(duration) is not int or duration != 1):
            raise ValueError("震动试验时长固定为 1 秒，避免持续输出。")
        code = 0x12 if kind == "suction" else 0x09
        waiter = asyncio.get_running_loop().create_future()
        entry = (code, level, waiter)
        started = None
        vibration_test = False
        try:
            async with self.gate:
                if not self.connected:
                    raise ValueError("请先连接 TF-PTPRO。")
                if kind == "suction" and time.monotonic() - self.last_suction < 1.5:
                    raise ValueError("请稍等片刻再触发下一次吮吸。")
                payload = (0x10, 1, 0x12, level) if kind == "suction" else (0x07, 1, 0x09, level)
                data = self.packet(payload)
                self.waiters.append(entry)
                await self.client.write_gatt_char(ACTION, data, response=False)
                started = time.monotonic()
                vibration_test = kind == "vibration" and duration == 1
                label = "单次吮吸" if kind == "suction" else ("震动 1 秒测试" if vibration_test else "持续震动")
                self.add_log("info", f"{label} {level}% 指令已写入。", data.hex(" "))
                if kind == "suction":
                    self.last_suction = started
            try:
                try:
                    reply = await asyncio.wait_for(waiter, timeout=1.2 if kind == "suction" else 1.0)
                    self.add_log("success", "设备已回报对应动作。", reply)
                    acknowledged = True
                except asyncio.TimeoutError:
                    self.add_log("info", "指令已写入，暂未收到对应动作通知。")
                    acknowledged = False
                if vibration_test:
                    remaining = 1.0 - (time.monotonic() - started)
                    if remaining > 0:
                        await asyncio.sleep(remaining)
            finally:
                if vibration_test and self.connected:
                    async with self.gate:
                        await self.stop_locked("1 秒震动测试结束，停止指令已发送。")
            return {"sent": True, "acknowledged": acknowledged, "state": await self.snapshot()}
        finally:
            if entry in self.waiters:
                self.waiters.remove(entry)

    async def dispatch(self, route, data):
        self.last_ui = time.monotonic()
        if route == "/api/status":
            return await self.snapshot(touch=True)
        if route == "/api/scan":
            return await self.scan()
        if route == "/api/connect":
            return await self.connect(data.get("address"))
        if route == "/api/disconnect":
            return await self.disconnect()
        if route == "/api/stop":
            return await self.stop()
        if route == "/api/stop-vibration":
            return await self.stop_vibration()
        if route in ("/api/suction", "/api/vibration"):
            return await self.action(route.rsplit("/", 1)[1], data.get("level"), data.get("duration", 1))
        if route == "/api/mcp/vibration":
            return await self.action("vibration", data.get("level"), duration=None)
        raise ValueError("未知操作。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runtime", default=None)
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    loop = asyncio.new_event_loop()
    controller = Controller()
    worker = threading.Thread(target=loop.run_forever, daemon=True)
    worker.start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, body, mime="application/json; charset=utf-8"):
            content = json.dumps(body, ensure_ascii=False).encode() if isinstance(body, dict) else body
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(content)

        def authorized(self):
            if not secrets.compare_digest(self.headers.get("X-Control-Token", ""), token):
                self.reply(401, {"error": "请通过启动器生成的链接打开控制台。"})
                return False
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self.reply(403, {"error": "请求来源不匹配。"})
                return False
            return True

        def api(self, route, data):
            if not self.authorized():
                return
            future = asyncio.run_coroutine_threadsafe(controller.dispatch(route, data), loop)
            try:
                self.reply(200, {"ok": True, "result": future.result(timeout=50)})
            except Exception as exc:
                if not future.done():
                    future.cancel()
                message = str(exc) or "操作失败，请检查蓝牙适配器。"
                if "failed to discover services" in message.lower():
                    message = "设备在读取蓝牙服务时断开，请确认设备仍开机后再次连接。"
                elif "no powered bluetooth" in message.lower() or "org.bluez.error.notready" in message.lower():
                    message = "蓝牙适配器未就绪，请检查系统蓝牙电源和适配器状态。"
                loop.call_soon_threadsafe(controller.add_log, "error", message)
                self.reply(400, {"error": message})

        def do_GET(self):
            route = urlsplit(self.path).path
            if route == "/api/status":
                return self.api(route, {})
            if route == "/health":
                return self.reply(200, {"ok": True, "device": NAME})
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8")}
            if route not in files:
                return self.reply(404, {"error": "Not found"})
            name, mime = files[route]
            self.reply(200, (STATIC / name).read_bytes(), mime)

        def do_POST(self):
            route = urlsplit(self.path).path
            if route not in {"/api/scan", "/api/connect", "/api/disconnect", "/api/suction", "/api/vibration", "/api/stop", "/api/stop-vibration", "/api/mcp/vibration"}:
                return self.reply(404, {"error": "Not found"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 2048 or self.headers.get_content_type() != "application/json":
                    raise ValueError("请求需要有效的 JSON 数据。")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("请求需要 JSON 对象。")
            except (ValueError, UnicodeError) as exc:
                return self.reply(400, {"error": str(exc)})
            self.api(route, data)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    runtime = Path(args.runtime or (Path(tempfile.gettempdir()) / f"tfptpro-web-{args.port}.json"))
    runtime.write_text(json.dumps({"pid": os.getpid(), "port": args.port, "token": token,
                                  "url": f"http://localhost:{args.port}/?session={os.getpid()}#token={token}"}))
    runtime.chmod(0o600)
    print(f"TF-PTPRO Web ready on port {args.port}; runtime: {runtime}", flush=True)
    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(controller.disconnect(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        worker.join(timeout=2)
        if runtime.exists():
            runtime.unlink()


if __name__ == "__main__":
    main()
