"""Async BLE device management for the verified DokiDoki profile."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Iterable

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakDeviceNotFoundError, BleakError
from bleak.backends.device import BLEDevice

from .protocol import (
    CHAR_NOTIFY,
    CHAR_WRITE_NO_RESPONSE,
    CHAR_WRITE_RESPONSE,
    DEFAULT_DEVICE_NAME,
    DEVICE_PREFIXES,
    SERVICE_UUID,
    DeviceProfile,
    MessageIdGenerator,
    build_action_frame,
    build_command_frame,
    get_device_profile,
    is_supported_device_name,
    CMD_HEARTBEAT,
    TF_PTPRO_PROFILE,
    build_stop_frame,
)

LOGGER = logging.getLogger(__name__)

# Keep the stop order identical to the installed client's ACTIONS_TO_ZERO.
STOP_ACTION_ORDER = ("linear", "rotary", "vibration")


class DokiDokiError(Exception):
    """Base error with a stable machine-readable code."""

    code = "device_error"


class DeviceNotConnectedError(DokiDokiError):
    code = "not_connected"


class DeviceNotFoundError(DokiDokiError):
    code = "device_not_found"


class UnsupportedDeviceError(DokiDokiError):
    code = "unsupported_device"


class GattError(DokiDokiError):
    code = "gatt_error"


class ConfirmationRequiredError(DokiDokiError):
    code = "confirmation_required"


@dataclass(frozen=True)
class DeviceRecord:
    name: str
    address: str
    rssi: int | None = None
    source: str = "scan"
    connected: bool = False
    service_uuids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "address": self.address,
            "rssi": self.rssi,
            "source": self.source,
            "connected": self.connected,
            "supported": get_device_profile(self.name) is not None,
            "profile": (get_device_profile(self.name).name if get_device_profile(self.name) else None),
            "service_uuids": list(self.service_uuids),
        }


def _uuid_matches(value: str | None, short_uuid: str) -> bool:
    normalized = str(value or "").lower()
    short = short_uuid.lower()
    return normalized == short or normalized == f"0000{short}-0000-1000-8000-00805f9b34fb"


def _normalize_address(value: str | None) -> str:
    raw = re.sub(r"[^0-9a-fA-F]", "", str(value or ""))
    if len(raw) != 12:
        return str(value or "").strip().upper()
    return ":".join(raw[index : index + 2] for index in range(0, 12, 2)).upper()


def _decode_registry_name(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").rstrip("\x00").strip()
    if isinstance(value, (list, tuple)):
        try:
            return bytes(value).decode("utf-8", errors="ignore").rstrip("\x00").strip()
        except (TypeError, ValueError):
            return ""
    return str(value or "").rstrip("\x00").strip()


def _cached_windows_devices() -> list[DeviceRecord]:
    """Read cached BLE names on Windows when a device is not advertising.

    Web Bluetooth devices can remain connected without appearing in a fresh
    advertisement scan.  The official app's current DK-META2 is present in
    this cache, so using it as a discovery fallback avoids hard-coding a MAC.
    """

    if sys.platform != "win32":
        return []
    try:
        import winreg

        base = r"SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices"
        root = winreg.ConnectRegistry(None, winreg.HKEY_LOCAL_MACHINE)
        with winreg.OpenKey(root, base) as devices_key:
            records: list[DeviceRecord] = []
            for index in range(winreg.QueryInfoKey(devices_key)[0]):
                try:
                    subkey_name = winreg.EnumKey(devices_key, index)
                    with winreg.OpenKey(devices_key, subkey_name) as device_key:
                        name = ""
                        for candidate in ("LEName", "Name"):
                            try:
                                name = _decode_registry_name(winreg.QueryValueEx(device_key, candidate)[0])
                            except OSError:
                                continue
                            if name:
                                break
                        if is_supported_device_name(name):
                            records.append(
                                DeviceRecord(
                                    name=name,
                                    address=_normalize_address(subkey_name),
                                    source="windows-cache",
                                )
                            )
                except OSError:
                    continue
            return records
    except (ImportError, OSError):
        return []


class DokiDokiDeviceManager:
    """Own one BLE connection and expose safe high-level device actions."""

    def __init__(self) -> None:
        self.preferred_name = os.getenv("DOKIDOKI_DEVICE_NAME", DEFAULT_DEVICE_NAME)
        self.preferred_address = _normalize_address(os.getenv("DOKIDOKI_DEVICE_ADDRESS"))
        self._client: BleakClient | None = None
        self._device_name: str | None = None
        self._device_address: str | None = None
        self._profile: DeviceProfile | None = None
        self._write_response: Any | None = None
        self._write_no_response: Any | None = None
        self._notify: Any | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._io_lock = asyncio.Lock()
        self._message_ids = MessageIdGenerator()
        self._last_error: str | None = None
        self._last_action: dict[str, Any] | None = None
        self._last_notify_at: float | None = None
        self._last_notification: str | None = None
        self._notification_waiters: list[tuple[int, int, asyncio.Future[bytes]]] = []

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    @property
    def profile(self) -> DeviceProfile | None:
        return self._profile

    async def list_devices(self, timeout: float = 5.0, prefix: str | None = None) -> dict[str, Any]:
        timeout = max(1.0, min(float(timeout), 20.0))
        prefix_upper = prefix.strip().upper() if prefix else None
        records: dict[str, DeviceRecord] = {}
        scan_error: str | None = None

        try:
            discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
            for device, advertisement in discovered.values():
                name = (device.name or advertisement.local_name or "").strip()
                if prefix_upper and not name.upper().startswith(prefix_upper):
                    continue
                address = _normalize_address(device.address)
                records[address] = DeviceRecord(
                    name=name,
                    address=address,
                    rssi=getattr(advertisement, "rssi", None),
                    source="scan",
                    connected=bool(self.is_connected and address == self._device_address),
                    service_uuids=tuple(sorted(str(item).lower() for item in (advertisement.service_uuids or []))),
                )
        except Exception as exc:  # Bleak backend errors should not hide cache results.
            scan_error = str(exc)
            LOGGER.warning("BLE scan failed: %s", exc)

        for cached in _cached_windows_devices():
            if prefix_upper and not cached.name.upper().startswith(prefix_upper):
                continue
            if cached.address not in records:
                records[cached.address] = cached

        if self.is_connected and self._device_address and self._device_name:
            records[self._device_address] = DeviceRecord(
                name=self._device_name,
                address=self._device_address,
                source="connected",
                connected=True,
            )

        devices = [record.as_dict() for record in sorted(records.values(), key=lambda item: item.name)]
        result: dict[str, Any] = {"devices": devices, "count": len(devices), "timeout_seconds": timeout}
        if scan_error:
            result["scan_warning"] = scan_error
        return result

    async def connect(self, address: str | None = None, name: str | None = None) -> dict[str, Any]:
        target_address = _normalize_address(address)
        target_name = (name or (self.preferred_name if not address else "")).strip()
        target_device: Any | None = None

        if not target_address:
            target_name = (name or self.preferred_name).strip()
            if self.preferred_address and (not name or name.upper() == self.preferred_name.upper()):
                target_address = self.preferred_address
            else:
                listing = await self.list_devices(timeout=5.0, prefix=target_name)
                candidates = listing["devices"]
                candidates = [item for item in candidates if item["name"].upper() == target_name.upper()]
                if not candidates:
                    raise DeviceNotFoundError(f"未找到设备 {name or self.preferred_name}。")
                target_address = _normalize_address(candidates[0]["address"])
                target_name = candidates[0]["name"]

        if self.is_connected and self._device_address == target_address:
            return self.status()
        if self.is_connected:
            await self.disconnect(send_stop=True)

        try:
            discovered = await BleakScanner.discover(timeout=3.0, return_adv=True)
            for device, advertisement in discovered.values():
                if _normalize_address(device.address) == target_address:
                    target_device = device
                    target_name = (device.name or advertisement.local_name or target_name).strip()
                    break
        except Exception as exc:
            LOGGER.info("BLE pre-scan unavailable; using address directly: %s", exc)

        profile = get_device_profile(target_name)
        if profile is None:
            raise UnsupportedDeviceError(f"暂不支持设备型号 {target_name or 'Unknown'}。")

        # Passing a BLEDevice object is important on Windows: Bleak can then
        # resolve the cached WinRT device directly instead of performing a new
        # advertisement scan. This matters while the official Electron app
        # already holds the device connection and the peripheral is not
        # advertising.
        if target_device is None and not target_name:
            raise DeviceNotFoundError(f"扫描中未找到所选地址 {target_address}，请重新扫描并传入设备名称。")
        target = target_device or BLEDevice(target_address, target_name, None)
        client = BleakClient(target, disconnected_callback=self._on_disconnect)
        try:
            await client.connect()
            if not client.is_connected:
                raise GattError("BLE 客户端未进入已连接状态。")
            service = self._find_service(client, SERVICE_UUID)
            if service is None:
                available = [str(item.uuid) for item in client.services]
                if available and all(_uuid_matches(item, "1800") or _uuid_matches(item, "1801") for item in available):
                    raise GattError(
                        "未找到 DokiDoki GATT 服务 FFAC；当前只返回标准服务，"
                        "通常表示官方心跳元力客户端仍占用设备连接，请先在官方客户端断开设备。"
                    )
                suffix = f" 当前可见服务：{', '.join(available)}。" if available else " 当前没有可见 GATT 服务。"
                raise GattError(f"未找到 DokiDoki GATT 服务 FFAC。{suffix}")

            characteristics = list(service.characteristics)
            write_response = next((item for item in characteristics if _uuid_matches(item.uuid, CHAR_WRITE_RESPONSE)), None)
            write_no_response = next((item for item in characteristics if _uuid_matches(item.uuid, CHAR_WRITE_NO_RESPONSE)), None)
            notify = next((item for item in characteristics if _uuid_matches(item.uuid, CHAR_NOTIFY)), None)
            if write_response is None and write_no_response is None:
                raise GattError("未找到 DokiDoki 写入特征 FFB5/FFB7。")
            if profile.protocol == TF_PTPRO_PROFILE.protocol and (write_response is None or write_no_response is None or notify is None):
                raise GattError("TF-PTPRO 协议匹配不完整；需要 FFB5、FFB7 和 FFB8 特征。")

            self._client = client
            self._device_name = target_name or DEFAULT_DEVICE_NAME
            self._device_address = target_address
            self._profile = profile
            self._write_response = write_response
            self._write_no_response = write_no_response
            self._notify = notify
            self._message_ids.reset()
            self._last_error = None
            self._last_action = None

            if notify is not None:
                try:
                    await client.start_notify(notify, self._on_notify)
                except Exception as exc:
                    LOGGER.info("Notify setup skipped: %s", exc)

            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="dokidoki-heartbeat")
            return self.status()
        except DokiDokiError:
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise
        except BleakDeviceNotFoundError as exc:
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise DeviceNotFoundError(f"无法找到蓝牙设备 {target_address}：{exc}") from exc
        except BleakError as exc:
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise GattError(f"蓝牙 GATT 连接失败：{exc}") from exc
        except Exception:
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise

    @staticmethod
    def _find_service(client: BleakClient, uuid: str) -> Any | None:
        """Find a service across Bleak backends and Bleak 3 collection shapes."""

        services = getattr(client, "services", None)
        if services is None:
            return None
        getter = getattr(services, "get_service", None)
        if getter is not None:
            service = getter(uuid)
            if service is not None:
                return service
        for service in services:
            if _uuid_matches(getattr(service, "uuid", None), uuid):
                return service
        return None

    async def disconnect(self, send_stop: bool = True) -> dict[str, Any]:
        heartbeat = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat and heartbeat is not asyncio.current_task():
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

        if send_stop and self.is_connected:
            await self._best_effort_stop()

        client = self._client
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()

        self._client = None
        self._device_name = None
        self._device_address = None
        self._profile = None
        self._write_response = None
        self._write_no_response = None
        self._notify = None
        self._message_ids.reset()
        return self.status()

    async def shutdown(self) -> None:
        await self.disconnect(send_stop=True)

    def status(self) -> dict[str, Any]:
        profile = self._profile
        return {
            "connected": self.is_connected,
            "device": {
                "name": self._device_name,
                "address": self._device_address,
            },
            "profile": profile.name if profile else None,
            "protocol": profile.protocol if profile else None,
            "capabilities": list(profile.capabilities) if profile else [],
            "gatt": {
                "service": SERVICE_UUID if profile else None,
                "write_with_response": str(getattr(self._write_response, "uuid", "")) or None,
                "write_without_response": str(getattr(self._write_no_response, "uuid", "")) or None,
                "notify": str(getattr(self._notify, "uuid", "")) or None,
            },
            "heartbeat_running": bool(self._heartbeat_task and not self._heartbeat_task.done()),
            "last_action": self._last_action,
            "last_notification": self._last_notification,
            "last_error": self._last_error,
        }

    async def set_action(self, action: str, level: int, confirm: bool = False) -> dict[str, Any]:
        if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 100:
            raise ValueError("level 必须是 0 到 100 的整数。")
        if level > 0 and not confirm:
            raise ConfirmationRequiredError("非零设备动作需要 confirm=true。")
        profile = self._require_profile()
        if action not in profile.capabilities:
            raise UnsupportedDeviceError(f"{profile.name} 不支持 {action}。")
        if action == "suction" and level == 0:
            raise ValueError("单次吮吸强度必须是 1–100；停止操作请调用 stop_device。")
        is_tf_vibration = action == "vibration" and profile.protocol == TF_PTPRO_PROFILE.protocol
        if is_tf_vibration and level == 0:
            raise ValueError("TF-PTPRO 震动强度必须是 1–100；停止操作请调用 stop_device。")

        action_code = profile.commands[action]
        waiter = asyncio.get_running_loop().create_future()
        entry = (action_code, level, waiter)
        action_notification: str | None = None
        try:
            async with self._io_lock:
                frame = build_action_frame(self._message_ids.next(), action, level, profile)
                self._notification_waiters.append(entry)
                await self._write_control(frame)
                self._last_action = {"action": action, "level": level, "frame": frame.hex(" ")}
            try:
                notification = await asyncio.wait_for(waiter, timeout=1.2)
                acknowledged = True
                action_notification = notification.hex(" ")
                self._last_notification = action_notification
            except asyncio.TimeoutError:
                acknowledged = False
            return {"ok": True, "action": action, "level": level, "profile": profile.name,
                    "acknowledged": acknowledged,
                    "notification": action_notification if acknowledged else None,
                    "duration_seconds": None,
                    "auto_stop": None,
                    "meaning": "设备回报了对应命令和值；这不测量物理力度或输出。"}
        finally:
            if entry in self._notification_waiters:
                self._notification_waiters.remove(entry)

    async def stop(self) -> dict[str, Any]:
        if not self.is_connected:
            return {"ok": True, "stopped": False, "reason": "not_connected"}
        profile = self._require_profile()
        if profile.protocol == TF_PTPRO_PROFILE.protocol:
            async with self._io_lock:
                try:
                    await self._write_control(build_stop_frame(self._message_ids.next(), profile))
                    self._last_action = {"action": "stop", "level": 0, "actions": list(profile.capabilities)}
                    return {"ok": True, "stopped": True, "actions": list(profile.capabilities), "profile": profile.name}
                except Exception as exc:
                    self._last_error = str(exc)
                    return {"ok": False, "stopped": False, "error": str(exc), "profile": profile.name}
        actions: list[str] = []
        async with self._io_lock:
            for action in STOP_ACTION_ORDER:
                if action not in profile.capabilities:
                    continue
                frame = build_action_frame(self._message_ids.next(), action, 0, profile)
                try:
                    await self._write_control(frame)
                    actions.append(action)
                except Exception as exc:
                    self._last_error = str(exc)
                    LOGGER.warning("Stop action %s failed: %s", action, exc)
            self._last_action = {"action": "stop", "level": 0, "actions": actions}
        return {"ok": len(actions) == len(profile.capabilities), "stopped": True, "actions": actions, "profile": profile.name}

    async def stop_vibration(self) -> dict[str, Any]:
        if not self.is_connected:
            return {"ok": True, "stopped": False, "reason": "not_connected"}
        profile = self._require_profile()
        if "vibration" not in profile.capabilities:
            raise UnsupportedDeviceError(f"{profile.name} 不支持 vibration。")
        async with self._io_lock:
            try:
                frame = build_action_frame(self._message_ids.next(), "vibration", 0, profile)
                await self._write_control(frame)
                self._last_action = {"action": "stop_vibration", "level": 0, "frame": frame.hex(" ")}
                return {"ok": True, "stopped": True, "actions": ["vibration"], "profile": profile.name,
                        "frame": frame.hex(" ")}
            except Exception as exc:
                self._last_error = str(exc)
                return {"ok": False, "stopped": False, "error": str(exc), "profile": profile.name}

    def _require_profile(self) -> DeviceProfile:
        if not self.is_connected or self._profile is None:
            raise DeviceNotConnectedError("设备未连接；请先调用 connect_device。")
        return self._profile

    async def _write_control(self, frame: bytes) -> None:
        if not self.is_connected:
            raise DeviceNotConnectedError("设备连接已断开。")
        characteristic = self._write_no_response or self._write_response
        if characteristic is None or self._client is None:
            raise GattError("设备写入特征不可用。")
        await self._client.write_gatt_char(characteristic, frame, response=characteristic is self._write_response)

    async def _write_heartbeat(self) -> None:
        if not self.is_connected or self._client is None:
            return
        characteristic = self._write_response or self._write_no_response
        if characteristic is None:
            return
        frame = build_command_frame(self._message_ids.next(), CMD_HEARTBEAT, 0x01)
        await self._client.write_gatt_char(characteristic, frame, response=characteristic is self._write_response)

    async def _heartbeat_loop(self) -> None:
        try:
            while self.is_connected:
                await asyncio.sleep(2.0)
                if not self.is_connected:
                    return
                try:
                    async with self._io_lock:
                        await self._write_heartbeat()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_error = str(exc)
                    LOGGER.warning("DokiDoki heartbeat failed: %s", exc)
                    await self._best_effort_stop()
                    await self.disconnect(send_stop=False)
                    return
        except asyncio.CancelledError:
            raise

    async def _best_effort_stop(self) -> None:
        if not self.is_connected or self._profile is None:
            return
        try:
            async with self._io_lock:
                if self._profile.protocol == TF_PTPRO_PROFILE.protocol:
                    with contextlib.suppress(Exception):
                        await self._write_control(build_stop_frame(self._message_ids.next(), self._profile))
                    return
                for action in STOP_ACTION_ORDER:
                    if action not in self._profile.capabilities:
                        continue
                    frame = build_action_frame(self._message_ids.next(), action, 0, self._profile)
                    with contextlib.suppress(Exception):
                        await self._write_control(frame)
        except Exception as exc:
            self._last_error = str(exc)

    def _on_disconnect(self, _client: BleakClient) -> None:
        self._last_error = "设备连接已断开。"

    def _on_notify(self, _characteristic: Any, _data: bytearray) -> None:
        self._last_notify_at = asyncio.get_running_loop().time()
        data = bytes(_data)
        self._last_notification = data.hex(" ")
        if len(data) >= 7 and data[1] == 0x07:
            for code, value, future in list(self._notification_waiters):
                if data[4:6] == bytes((code, value)) and not future.done():
                    future.set_result(data)
