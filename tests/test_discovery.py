import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gaia_transport import _list_paired_windows


class WindowsDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.bluetooth = Mock()
        self.enumeration = Mock()
        self.bluetooth.get_device_selector_from_pairing_state.return_value = "paired-bluetooth"
        self.entries = []
        self.enumeration.find_all_async_aqs_filter.return_value.get.return_value = self.entries
        self.addCleanup(patch.stopall)
        patch("gaia_transport.HAS_WINRT", True).start()
        patch("gaia_transport.BluetoothDevice", self.bluetooth, create=True).start()
        patch.dict("sys.modules", {
            "winrt.windows.devices.enumeration": SimpleNamespace(DeviceInformation=self.enumeration),
        }).start()

    def device(self, address, paired=True, name="MOMENTUM 4"):
        device = Mock(bluetooth_address=address)
        device.name = name
        device.device_information.pairing.is_paired = paired
        return device

    def enumerate_devices(self, devices):
        self.entries.extend(SimpleNamespace(id=f"system-guid-{i}", name="Fallback") for i in range(len(devices)))
        self.bluetooth.from_id_async.side_effect = [Mock(get=Mock(return_value=device)) for device in devices]
        return _list_paired_windows()

    def test_reads_real_addresses_deduplicates_and_closes_handles(self):
        devices = [self.device(0x001122AABBCC), self.device(0x001122AABBCC)]
        self.assertEqual(self.enumerate_devices(devices), [
            {"name": "MOMENTUM 4", "address": "00:11:22:AA:BB:CC"},
        ])
        self.bluetooth.get_device_selector_from_pairing_state.assert_called_once_with(True)
        self.enumeration.find_all_async_aqs_filter.assert_called_once_with("paired-bluetooth")
        for device in devices:
            device.close.assert_called_once_with()

    def test_skips_missing_unpaired_and_invalid_addresses(self):
        devices = [None, self.device(123, paired=False), self.device(0), self.device(1 << 48)]
        self.assertEqual(self.enumerate_devices(devices), [])
        for device in devices[1:]:
            device.close.assert_called_once_with()

    def test_one_inaccessible_device_does_not_hide_other_devices(self):
        self.entries.extend([SimpleNamespace(id="missing", name=""), SimpleNamespace(id="valid", name="")])
        valid = self.device(0xAABBCCDDEEFF)
        self.bluetooth.from_id_async.side_effect = [OSError("device removed"), Mock(get=Mock(return_value=valid))]
        with patch("sys.stderr"):
            self.assertEqual(_list_paired_windows(), [{"name": "MOMENTUM 4", "address": "AA:BB:CC:DD:EE:FF"}])
        valid.close.assert_called_once_with()

    def test_enumeration_failure_is_reported_without_scanning_all_system_devices(self):
        self.enumeration.find_all_async_aqs_filter.return_value.get.side_effect = OSError("Bluetooth unavailable")
        with self.assertRaisesRegex(OSError, "Bluetooth unavailable"):
            _list_paired_windows()
        self.enumeration.find_all_async_device_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
