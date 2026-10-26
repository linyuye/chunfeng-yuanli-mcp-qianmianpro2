"""MCP manager proxy for the authenticated local TF-PTPRO web controller.

This mode keeps one BLE owner in WSL and lets a Windows MCP process reuse it
through the controller's token-protected loopback/LAN API.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .device_manager import DokiDokiError


class WebBridgeError(DokiDokiError):
    """The WSL web controller is unavailable or rejected a request."""


class WebControllerBridge:
    def __init__(self) -> None:
        self.distribution = os.getenv("TFPTPRO_WSL_DISTRIBUTION", "Ubuntu-24.04")
        self.runtime_path = os.getenv("TFPTPRO_WEB_RUNTIME", "/tmp/tfptpro-web.json")
        self.preferred_name = os.getenv("DOKIDOKI_DEVICE_NAME", "TF-PTPRO")
        self.preferred_address = os.getenv("DOKIDOKI_DEVICE_ADDRESS", "")

    def _endpoint(self) -> tuple[str, str]:
        script = (
            "import json,subprocess;"
            f"p=json.load(open({self.runtime_path!r}));"
            "ips=subprocess.check_output(['hostname','-I'],text=True).split();"
            "print(json.dumps({'runtime':p,'ips':ips}))"
        )
        try:
            result = subprocess.run(
                ["wsl.exe", "-d", self.distribution, "--", "python3", "-c", script],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4,
            )
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            runtime = payload["runtime"]
            port = int(runtime["port"])
            addresses = payload.get("ips") or []
            base = f"http://{addresses[0]}:{port}" if addresses else f"http://127.0.0.1:{port}"
            return base, str(runtime["token"])
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise WebBridgeError(
                "无法读取 WSL 控制器状态；请确认 Ubuntu-24.04 中的 tfptpro-web 服务正在运行。"
            ) from exc

    def _request(self, route: str, data: dict[str, Any] | None = None) -> Any:
        base, token = self._endpoint()
        encoded = None if data is None else json.dumps(data).encode("utf-8")
        headers = {"X-Control-Token": token}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        request = Request(base + route, data=encoded, headers=headers, method="POST" if encoded is not None else "GET")
        try:
            with urlopen(request, timeout=50) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                body = {}
            message = body.get("error", "Web 控制器拒绝了请求。") if isinstance(body, dict) else str(body)
            raise WebBridgeError(str(message)) from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise WebBridgeError(f"无法连接 WSL 控制器：{exc}") from exc
        if not body.get("ok"):
            error = body.get("error") or {}
            raise WebBridgeError(error.get("message", "Web 控制器请求失败。"))
        return body.get("result")

    async def _call(self, route: str, data: dict[str, Any] | None = None) -> Any:
        return await asyncio.to_thread(self._request, route, data)

    async def list_devices(self, timeout: float = 8.0, prefix: str | None = None) -> dict[str, Any]:
        del timeout  # The web controller uses its fixed, tested 8-second BLE scan.
        scan_warning = None
        try:
            state = await self._call("/api/scan", {})
        except WebBridgeError:
            # The controller intentionally cannot scan during a live GATT
            # connection. Return its last discovered list plus the connected
            # device so MCP clients can still inspect the current selection.
            state = await self._call("/api/status")
            if not state.get("connected"):
                raise
            scan_warning = "控制器当前已有活动 BLE 连接，返回最近一次扫描列表；断开后可重新扫描。"
        devices = state.get("devices", [])
        if prefix:
            devices = [item for item in devices if item.get("name", "").upper().startswith(prefix.upper())]
        formatted = []
        for item in devices:
            connected = bool(item.get("connected")) or (
                state.get("connected") and item.get("address") == state.get("address")
            )
            matched = connected and bool(state.get("protocol_match"))
            formatted.append({
                "name": item.get("name", ""),
                "address": item.get("address", ""),
                "rssi": item.get("rssi"),
                "source": item.get("source", "scan"),
                "connected": connected,
                "supported": item.get("name", "").upper() == "TF-PTPRO" or matched,
                "profile": "TF-PTPRO" if item.get("name", "").upper() == "TF-PTPRO" or matched else None,
                "protocol_candidate": bool(item.get("protocol_candidate")),
                "service_uuids": item.get("service_uuids", []),
            })
        result = {"devices": formatted, "count": len(formatted), "timeout_seconds": 8.0}
        if scan_warning:
            result["scan_warning"] = scan_warning
        return result

    def _status(self, state: dict[str, Any]) -> dict[str, Any]:
        connected = bool(state.get("connected"))
        device = {"name": state.get("device_name"), "address": state.get("address")}
        matched = connected and bool(state.get("protocol_match"))
        return {
            "connected": connected,
            "device": device,
            "profile": "TF-PTPRO" if matched else None,
            "protocol": "dokidoki-tfptpro" if matched else None,
            "capabilities": list(state.get("available_actions", [])),
            "gatt": {"service": "0000ffac-0000-1000-8000-00805f9b34fb" if matched else None},
            "heartbeat_running": bool(state.get("heartbeat_running", False)),
            "last_action": None,
            "last_notification": (state.get("notifications") or [{}])[-1].get("data"),
            "last_error": None,
        }

    async def connect(self, address: str | None = None, name: str | None = None) -> dict[str, Any]:
        del name  # GATT matching is authoritative in the web controller.
        if not address:
            raise WebBridgeError("请先扫描并显式提供设备 BLE 地址。")
        current = await self._call("/api/status")
        if current.get("connected"):
            if str(current.get("address", "")).casefold() == address.casefold():
                return self._status(current)
            raise WebBridgeError("当前已有设备连接；请先断开，再扫描并连接所选设备。")
        return self._status(await self._call("/api/connect", {"address": address}))

    async def status(self) -> dict[str, Any]:
        return self._status(await self._call("/api/status"))

    async def set_action(self, action: str, level: int, confirm: bool = False) -> dict[str, Any]:
        if action not in {"suction", "vibration"}:
            raise WebBridgeError("TF-PTPRO 当前只开放吮吸和震动。")
        if level > 0 and not confirm:
            raise WebBridgeError("非零设备动作需要 confirm=true。")
        route = "/api/suction" if action == "suction" else "/api/mcp/vibration"
        data = {"level": level}
        result = await self._call(route, data)
        state = result.get("state", {})
        logs = state.get("logs", [])
        prefix = f"单次吮吸 {level}%" if action == "suction" else f"持续震动 {level}%"
        written = next((item for item in reversed(logs) if item.get("message", "").startswith(prefix)), {})
        notification = next((item for item in reversed(logs)
                             if item.get("message", "").startswith("设备已回报对应动作")), {})
        return {
            "ok": bool(result.get("sent")),
            "action": action,
            "level": level,
            "duration_seconds": None,
            "profile": "TF-PTPRO",
            "acknowledged": bool(result.get("acknowledged")),
            "frame": written.get("detail"),
            "notification": notification.get("detail"),
            "auto_stop_frame": None,
            "meaning": "设备回报对应命令和值；这不测量物理力度或输出。",
        }

    async def stop(self) -> dict[str, Any]:
        state = await self._call("/api/stop", {})
        return {"ok": True, "stopped": True, "actions": ["suction"], "profile": "TF-PTPRO",
                "last_log": (state.get("logs") or [{}])[-1].get("message")}

    async def stop_vibration(self) -> dict[str, Any]:
        state = await self._call("/api/stop-vibration", {})
        return {"ok": True, "stopped": True, "actions": ["vibration"], "profile": "TF-PTPRO",
                "last_log": (state.get("logs") or [{}])[-1].get("message")}

    async def disconnect(self, send_stop: bool = True) -> dict[str, Any]:
        del send_stop  # The controller always attempts stop before disconnect.
        state = await self._call("/api/disconnect", {})
        return self._status(state)

    async def shutdown(self) -> None:
        # MCP is a client of the web-owned BLE session; closing MCP must not
        # unexpectedly disconnect a device from the open web console.
        return None
