import asyncio
import tempfile
import unittest
from pathlib import Path

from heartbeat_yuanli_mcp.waveforms import WaveService, WaveStore, sine_events, validate_events


class FakeManager:
    def __init__(self):
        self.actions = []

    def status(self):
        return {"connected": True, "capabilities": ["suction", "vibration"]}

    async def set_action(self, action, level, confirm=False):
        self.actions.append((action, level))
        return {"ok": True, "acknowledged": True}

    async def stop_vibration(self):
        self.actions.append(("stop_vibration", None))
        return {"ok": True, "acknowledged": True}

    async def stop(self):
        self.actions.append(("stop_device", None))
        return {"ok": True}


class WaveformTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = WaveStore(Path(self.temp.name) / "waves.sqlite3")
        self.manager = FakeManager()
        self.service = WaveService(self.manager, self.store)

    async def asyncTearDown(self):
        await self.service.shutdown()
        self.temp.cleanup()

    def test_sine_generation_and_validation(self):
        events = sine_events(6, 6, 10, 90, 2, 20)
        self.assertEqual([e["action"] for e in events], ["vibration", "suction", "suction", "suction", "suction"])
        self.assertEqual([e["level"] for e in events[1:]], [50, 85, 15, 50])
        with self.assertRaises(ValueError):
            validate_events([{"at": 0, "action": "suction", "level": 5},
                             {"at": 1, "action": "suction", "level": 10}])

    async def test_playback_leaves_vibration_running_then_independent_stop(self):
        self.service.save_waveform("quick", [{"at": 0, "action": "vibration", "level": 5},
                                                {"at": 0, "action": "suction", "level": 5}])
        result = await self.service.start("quick", True)
        self.assertEqual(result["state"], "playing")
        while self.service.running:
            await asyncio.sleep(0.01)
        self.assertEqual(self.service.status()["state"], "completed")
        self.assertEqual(self.manager.actions, [("vibration", 5), ("suction", 5)])
        await self.service.stop(stop_vibration=True)
        self.assertEqual(self.manager.actions[-1], ("stop_vibration", None))

    async def test_recording_is_local_and_convertible(self):
        self.service.start_recording()
        await self.service.perform("suction", 15, confirm=True)
        recording = await self.service.stop_recording("sample")
        self.assertTrue(recording["events"][0]["acknowledged"])
        waveform = self.service.save_recording_as_waveform("sample", "replay")
        self.assertEqual(waveform["events"][0]["action"], "suction")


if __name__ == "__main__":
    unittest.main()
