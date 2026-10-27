"""Control protocol and timed shutdown checks; no physical BLE writes."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from server import ACTION, SERVICE, Controller, frame


class FakeClient:
    is_connected = True

    def __init__(self, controller, fail_stop=False):
        self.controller = controller
        self.frames = []
        self.fail_stop = fail_stop

    async def write_gatt_char(self, uuid, data, response):
        self.frames.append((uuid, data, response))
        if data[3] == 9 and self.fail_stop:
            raise OSError('link lost')
        if data[4:7] == bytes((0x10, 1, 0x12)):
            reply = bytes((5, 7, 0, 3, 0x12, data[7], (-0x12-data[7]) & 255))
            asyncio.get_running_loop().call_soon(self.controller.notification, None, reply)
        if data[4:7] == bytes((7, 1, 9)):
            reply = bytes((5, 7, 0, 3, 9, data[7], (-9-data[7]) & 255))
            asyncio.get_running_loop().call_soon(self.controller.notification, None, reply)

    async def disconnect(self):
        self.is_connected = False


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.controller = Controller()
        self.client = FakeClient(self.controller)
        self.controller.client = self.client

    async def asyncTearDown(self):
        pass

    def test_frames_match_original_cli_capture(self):
        self.assertEqual(frame(3, bytes((0x10, 1, 0x12, 5))).hex(), '0302000510011205d8')
        self.assertEqual(frame(1, bytes((0x10, 1, 0x12, 10))).hex(), '010200051001120ad3')
        self.assertEqual(frame(2, bytes((0x10, 1, 0x12, 100))).hex(), '020200051001126479')
        self.assertEqual(frame(3, bytes((7, 1, 9, 5))).hex(), '0302000507010905ea')
        self.assertEqual(frame(4, bytes((7, 0, 0x10, 0, 9, 0, 0x12, 0))).hex(), '040200090700100009001200ce')

    async def test_bleak_021_connected_wrapper_is_serializable_bool(self):
        class OldBleakBool:
            def __bool__(self):
                return True
        self.client.is_connected = OldBleakBool()
        self.assertIs((await self.controller.snapshot())['connected'], True)

    async def test_suction_has_one_write_and_matches_notification(self):
        result = await self.controller.action('suction', 5)
        self.assertTrue(result['acknowledged'])
        self.assertEqual(len(self.client.frames), 1)
        self.assertEqual(self.client.frames[0][0], ACTION)
        self.assertEqual(self.controller.waiters, [])
        with self.assertRaises(ValueError):
            await self.controller.action('suction', 5)
        self.assertEqual(len(self.client.frames), 1)

    async def test_scan_lists_all_advertised_ble_devices(self):
        class Device:
            def __init__(self, address, name):
                self.address, self.name = address, name
        class Advertisement:
            rssi = -55
            local_name = None
            service_uuids = ['180F']
        class Scanner:
            @staticmethod
            async def discover(timeout, return_adv):
                return {
                    'one': (Device('02:00:00:00:00:03', 'Other BLE'), Advertisement()),
                    'two': (Device('02:00:00:00:00:01', 'TF-PTPRO'), Advertisement()),
                }
        with patch('server.BleakScanner', Scanner):
            result = await Controller().scan()
        self.assertEqual(len(result['devices']), 2)
        other = next(item for item in result['devices'] if item['name'] == 'Other BLE')
        target = next(item for item in result['devices'] if item['name'] == 'TF-PTPRO')
        self.assertFalse(other['protocol_candidate'])
        self.assertTrue(target['protocol_candidate'])

    async def test_vibration_test_is_one_second_and_stops_automatically(self):
        result = await self.controller.action('vibration', 5, duration=1)
        self.assertTrue(result['acknowledged'])
        self.assertEqual([frame.hex() for _, frame, _ in self.client.frames], [
            '0102000507010905ea',
            '020200090700100009001200ce',
        ])
        self.assertEqual(self.controller.waiters, [])

    async def test_vibration_test_rejects_longer_duration_before_writing(self):
        with self.assertRaisesRegex(ValueError, '固定为 1 秒'):
            await self.controller.action('vibration', 25, duration=2)
        self.assertEqual(self.client.frames, [])

    async def test_mcp_vibration_persists_until_independent_stop(self):
        result = await self.controller.action('vibration', 25, duration=None)
        self.assertTrue(result['acknowledged'])
        self.assertEqual(len(self.client.frames), 1)

    async def test_stop_vibration_writes_per_channel_zero(self):
        await self.controller.stop_vibration()
        self.assertEqual([data.hex() for _, data, _ in self.client.frames], ['0102000507000900f0'])

    async def test_bad_input_or_disconnection_never_writes(self):
        for value in (0, 101, True, 2.5, '5'):
            with self.assertRaises(ValueError):
                await self.controller.action('suction', value)
        self.client.is_connected = False
        with self.assertRaises(ValueError):
            await self.controller.action('suction', 5)
        self.assertEqual(self.client.frames, [])

    async def test_disconnect_even_if_stop_write_fails(self):
        self.client.fail_stop = True
        await self.controller.disconnect()
        self.assertFalse(self.client.is_connected)
        self.assertIsNone(self.controller.client)

    async def test_connection_keeps_discovery_alive_until_gatt_ready(self):
        controller = Controller()
        address = '02:00:00:00:00:02'
        controller.devices[address] = {'address': address, 'name': 'Other BLE', 'rssi': -40,
                                       'service_uuids': [SERVICE], 'protocol_candidate': True}
        class Scanner:
            active = False
            def __init__(self, detection_callback):
                self.callback = detection_callback
            async def start(self):
                Scanner.active = True
                self.callback(SimpleNamespace(name='Other BLE', address=address),
                              SimpleNamespace(local_name='Other BLE'))
            async def stop(self):
                Scanner.active = False
        class Client(FakeClient):
            def __init__(self, device, **kwargs):
                super().__init__(controller)
                self.is_connected = False
                class Services(list):
                    def get_characteristic(self, uuid):
                        return object()
                self.services = Services([SimpleNamespace(uuid=SERVICE)])
            async def connect(self):
                if not Scanner.active:
                    raise RuntimeError('BlueZ device object was removed')
                self.is_connected = True
            async def start_notify(self, uuid, callback):
                pass
        with patch('server.BleakScanner', Scanner), patch('server.BleakClient', Client):
            state = await controller.connect(address)
            self.assertEqual(state['device_name'], 'Other BLE')
            self.assertTrue(state['protocol_match'])
            self.assertIs(state['connected'], True)
            self.assertFalse(Scanner.active)
            self.assertEqual(len(controller.client.frames), 2)
            await controller.disconnect()

    async def test_unknown_gatt_is_rejected_without_control_writes(self):
        controller = Controller()
        address = '02:00:00:00:00:03'
        controller.devices[address] = {'address': address, 'name': 'Other BLE', 'rssi': -40,
                                       'service_uuids': [], 'protocol_candidate': False}
        class Scanner:
            def __init__(self, detection_callback): self.callback = detection_callback
            async def start(self): self.callback(SimpleNamespace(address=address), SimpleNamespace())
            async def stop(self): pass
        class Services(list):
            def get_characteristic(self, _uuid): return None
        class Client(FakeClient):
            def __init__(self, device, **kwargs):
                super().__init__(controller)
                self.is_connected = False
                self.services = Services([SimpleNamespace(uuid='0000180f-0000-1000-8000-00805f9b34fb')])
            async def connect(self): self.is_connected = True
        with patch('server.BleakScanner', Scanner), patch('server.BleakClient', Client):
            with self.assertRaisesRegex(ValueError, '未匹配 TF-PTPRO 控制协议'):
                await controller.connect(address)
        self.assertIsNone(controller.client)


if __name__ == '__main__':
    unittest.main()
