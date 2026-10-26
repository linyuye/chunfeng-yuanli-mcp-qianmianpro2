from __future__ import annotations

import asyncio
import unittest

from heartbeat_yuanli_mcp.device_manager import DokiDokiDeviceManager
from heartbeat_yuanli_mcp.protocol import TF_PTPRO_PROFILE


class FakeClient:
    is_connected = True

    def __init__(self, manager: DokiDokiDeviceManager) -> None:
        self.manager = manager
        self.frames: list[bytes] = []

    async def write_gatt_char(self, _characteristic, data: bytes, response: bool) -> None:
        self.frames.append(data)
        if data[4:7] in (bytes((0x10, 1, 0x12)), bytes((0x07, 1, 0x09))):
            code, level = data[6], data[7]
        else:
            code, level = data[4], data[5]
        if (code, level) == (0x12, 5):
            checksum = 0xDF
        elif code == 0x09:
            checksum = (-code - level - 0x0A) & 0xFF
        else:
            checksum = (-code - level) & 0xFF
        notification = bytes((0x06, 0x07, 0, 3, code, level, checksum))
        asyncio.get_running_loop().call_soon(self.manager._on_notify, None, bytearray(notification))


class DeviceManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_tf_ptpro_suction_returns_matching_device_notification(self) -> None:
        manager = DokiDokiDeviceManager()
        manager._profile = TF_PTPRO_PROFILE
        manager._write_no_response = object()
        client = FakeClient(manager)
        manager._client = client

        result = await manager.set_action("suction", 5, confirm=True)

        self.assertEqual(client.frames[0].hex(), "0102000510011205d8")
        self.assertTrue(result["acknowledged"])
        self.assertEqual(result["notification"], "06 07 00 03 12 05 df")
        self.assertEqual(manager._notification_waiters, [])

    async def test_tf_ptpro_vibration_persists_until_independent_stop(self) -> None:
        manager = DokiDokiDeviceManager()
        manager._profile = TF_PTPRO_PROFILE
        manager._write_no_response = object()
        manager._client = FakeClient(manager)

        result = await manager.set_action("vibration", 5, confirm=True)

        self.assertEqual(manager.profile.capabilities, ("suction", "vibration"))
        self.assertEqual(manager._client.frames[0].hex(), "0102000507010905ea")
        self.assertTrue(result["acknowledged"])
        self.assertEqual(result["notification"], "06 07 00 03 09 05 e8")
        self.assertEqual(len(manager._client.frames), 1)
        self.assertIsNone(result["auto_stop"])

    async def test_tf_ptpro_stop_vibration_sends_only_vibration_zero(self) -> None:
        manager = DokiDokiDeviceManager()
        manager._profile = TF_PTPRO_PROFILE
        manager._write_no_response = object()
        manager._client = FakeClient(manager)

        result = await manager.stop_vibration()

        self.assertTrue(result["stopped"])
        self.assertEqual(result["actions"], ["vibration"])
        self.assertEqual(manager._client.frames[0].hex(), "0102000507000900f0")

    async def test_tf_ptpro_suction_zero_requires_stop_tool(self) -> None:
        manager = DokiDokiDeviceManager()
        manager._profile = TF_PTPRO_PROFILE
        manager._client = FakeClient(manager)
        with self.assertRaisesRegex(ValueError, "stop_device"):
            await manager.set_action("suction", 0, confirm=False)


if __name__ == "__main__":
    unittest.main()
