from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from fastmcp import Client

from heartbeat_yuanli_mcp import server


class FakeManager:
    preferred_name = "TF-PTPRO"
    preferred_address = ""

    async def list_devices(self, timeout=5.0, prefix=None):
        return {"devices": [{"name": "TF-PTPRO 🌟", "connected": True}], "count": 1}

    async def shutdown(self):
        pass


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_doctor_output_is_encodable_on_gbk_console(self):
        output = io.StringIO()
        with patch.object(server, "_new_manager", return_value=FakeManager()), contextlib.redirect_stdout(output):
            result = await server._doctor()
        self.assertEqual(result, 0)
        output.getvalue().encode("gbk")
        self.assertEqual(json.loads(output.getvalue())["devices"]["devices"][0]["name"], "TF-PTPRO 🌟")

    async def test_mcp_scan_uses_lifespan_manager(self):
        manager = FakeManager()
        with patch.object(server, "_new_manager", return_value=manager) as factory:
            async with Client(server.mcp) as client:
                result = (await client.call_tool("list_devices", {})).data
        self.assertTrue(result["devices"][0]["connected"])
        factory.assert_called_once()
