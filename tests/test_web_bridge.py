from __future__ import annotations

import unittest

from heartbeat_yuanli_mcp.web_bridge import WebBridgeError, WebControllerBridge


class WebBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_lists_cached_devices_when_controller_is_connected(self) -> None:
        bridge = WebControllerBridge()
        calls: list[str] = []

        async def fake_call(route: str, _data=None):
            calls.append(route)
            if route == "/api/scan":
                raise WebBridgeError("已连接设备；断开后可重新扫描。")
            return {
                "connected": True,
                "device_name": "TF-PTPRO",
                "address": "60:28:0E:A4:9B:62",
                "protocol_match": True,
                "devices": [
                    {"name": "TF-PTPRO", "address": "60:28:0E:A4:9B:62", "source": "connected"},
                    {"name": "Nearby", "address": "AA:BB:CC:DD:EE:FF", "source": "scan"},
                ],
            }

        bridge._call = fake_call  # type: ignore[method-assign]
        result = await bridge.list_devices()

        self.assertEqual(calls, ["/api/scan", "/api/status"])
        self.assertEqual(result["count"], 2)
        self.assertIn("scan_warning", result)
        self.assertTrue(result["devices"][0]["connected"])
        self.assertEqual(result["devices"][0]["profile"], "TF-PTPRO")
        self.assertFalse(result["devices"][1]["connected"])

    async def test_scan_error_is_raised_when_controller_is_disconnected(self) -> None:
        bridge = WebControllerBridge()

        async def fake_call(route: str, _data=None):
            if route == "/api/scan":
                raise WebBridgeError("scan failed")
            return {"connected": False}

        bridge._call = fake_call  # type: ignore[method-assign]
        with self.assertRaisesRegex(WebBridgeError, "scan failed"):
            await bridge.list_devices()

    async def test_connect_rejects_switching_while_another_device_is_connected(self) -> None:
        bridge = WebControllerBridge()
        calls: list[tuple[str, dict | None]] = []

        async def fake_call(route: str, data=None):
            calls.append((route, data))
            return {"connected": True, "address": "60:28:0E:A4:9B:62", "protocol_match": True}

        bridge._call = fake_call  # type: ignore[method-assign]
        with self.assertRaisesRegex(WebBridgeError, "请先断开"):
            await bridge.connect(address="AA:BB:CC:DD:EE:FF")
        self.assertEqual(calls, [("/api/status", None)])

    async def test_connect_returns_status_for_the_already_connected_address(self) -> None:
        bridge = WebControllerBridge()

        async def fake_call(route: str, _data=None):
            self.assertEqual(route, "/api/status")
            return {
                "connected": True,
                "device_name": "TF-PTPRO",
                "address": "60:28:0E:A4:9B:62",
                "protocol_match": True,
                "available_actions": ["suction"],
            }

        bridge._call = fake_call  # type: ignore[method-assign]
        result = await bridge.connect(address="60:28:0E:A4:9B:62")
        self.assertEqual(result["device"]["name"], "TF-PTPRO")
        self.assertEqual(result["capabilities"], ["suction"])

    async def test_suction_returns_logged_device_notification(self) -> None:
        bridge = WebControllerBridge()

        async def fake_call(route: str, _data=None):
            self.assertEqual(route, "/api/suction")
            return {
                "sent": True,
                "acknowledged": True,
                "state": {
                    "logs": [
                        {"message": "单次吮吸 25% 指令已写入。", "detail": "01 02 ..."},
                        {"message": "设备已回报对应动作。", "detail": "0b 07 00 03 12 19 cb"},
                    ]
                },
            }

        bridge._call = fake_call  # type: ignore[method-assign]
        result = await bridge.set_action("suction", 25, confirm=True)

        self.assertTrue(result["acknowledged"])
        self.assertEqual(result["notification"], "0b 07 00 03 12 19 cb")

    async def test_tf_ptpro_vibration_is_routed_to_persistent_action(self) -> None:
        bridge = WebControllerBridge()
        calls = []

        async def fake_call(route: str, data=None):
            calls.append((route, data))
            return {
                "sent": True,
                "acknowledged": True,
                "state": {"logs": [
                    {"message": "持续震动 5% 指令已写入。", "detail": "01 02 ..."},
                    {"message": "设备已回报对应动作。", "detail": "05 07 00 03 09 05 e8"},
                ]},
            }

        bridge._call = fake_call  # type: ignore[method-assign]
        result = await bridge.set_action("vibration", 5, confirm=True)

        self.assertEqual(calls, [("/api/mcp/vibration", {"level": 5})])
        self.assertTrue(result["acknowledged"])
        self.assertIsNone(result["duration_seconds"])
        self.assertEqual(result["notification"], "05 07 00 03 09 05 e8")
        self.assertIsNone(result["auto_stop_frame"])

    async def test_stop_vibration_uses_independent_web_route(self) -> None:
        bridge = WebControllerBridge()
        calls = []

        async def fake_call(route: str, data=None):
            calls.append((route, data))
            return {"logs": [{"message": "仅震动输出已停止。"}]}

        bridge._call = fake_call  # type: ignore[method-assign]
        result = await bridge.stop_vibration()

        self.assertEqual(calls, [("/api/stop-vibration", {})])
        self.assertEqual(result["actions"], ["vibration"])


if __name__ == "__main__":
    unittest.main()
