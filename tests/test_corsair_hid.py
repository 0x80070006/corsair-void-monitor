import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from corsair_hid import led_report, parse_status_report, sidetone_report


class StatusReportTests(unittest.TestCase):
    def test_headset_off(self):
        status = parse_status_report([100, 0, 0, 52, 0])
        self.assertTrue(status.dongle_found)
        self.assertFalse(status.connected)
        self.assertIsNone(status.battery_percent)
        self.assertIsNone(status.microphone_raised)
        self.assertEqual(status.connection_state, "Casque éteint")

    def test_charging_reports_capped_percentage(self):
        # Real report captured from a charging VOID ELITE, microphone raised.
        status = parse_status_report([100, 0, 228, 177, 5])
        self.assertTrue(status.connected)
        self.assertTrue(status.charging)
        self.assertFalse(status.discharging)
        self.assertEqual(status.battery_percent, 100)
        self.assertTrue(status.microphone_raised)

    def test_discharging(self):
        status = parse_status_report([100, 0, 42, 177, 1])
        self.assertTrue(status.discharging)
        self.assertEqual(status.battery_percent, 42)
        self.assertFalse(status.microphone_raised)

    def test_undocumented_code_with_battery_is_connected(self):
        # Code 161 was seen on a live headset running on battery.
        status = parse_status_report([100, 0, 86, 161, 1])
        self.assertTrue(status.connected)
        self.assertEqual(status.battery_percent, 86)
        self.assertEqual(status.connection_state, "Connecté")

    def test_fully_charged_is_100(self):
        self.assertEqual(parse_status_report([100, 0, 127, 177, 4]).battery_percent, 100)

    def test_rejects_other_reports(self):
        with self.assertRaises(ValueError):
            parse_status_report([102, 0, 2, 0, 2])
        with self.assertRaises(ValueError):
            parse_status_report([100, 0])


class LedReportTests(unittest.TestCase):
    def test_matches_captured_icue_report(self):
        # Captured from iCUE for red on both logos, after the first report.
        expected = [0xCB, 6, 0x1C, 0xAF, 0x16, 0, 0x17, 0, 0x1D, 0xAF, 0x18, 0, 0x19, 0]
        self.assertEqual(led_report((255, 0, 0), (255, 0, 0)), expected + [0] * 6)

    def test_white_uses_full_gain_per_channel(self):
        self.assertEqual(
            led_report((255, 255, 255), (255, 255, 255))[:14],
            [0xCB, 6, 0x1C, 0xAF, 0x16, 0x97, 0x17, 0x78, 0x1D, 0xAF, 0x18, 0x97, 0x19, 0x78],
        )

    def test_left_and_right_are_independent(self):
        report = led_report((255, 0, 0), (0, 0, 255))
        self.assertEqual(report[3], 0xAF)
        self.assertEqual(report[9], 0)
        self.assertEqual(report[13], 0x78)

    def test_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            led_report((256, 0, 0), (0, 0, 0))


class SidetoneTests(unittest.TestCase):
    def test_bytes_and_range(self):
        report = sidetone_report(0)
        self.assertEqual(len(report), 64)
        self.assertEqual(report[:12], [0xFF, 0x0B, 0, 0xFF, 4, 0x0E, 0xFF, 5, 1, 4, 0, 200])
        self.assertEqual(sidetone_report(55)[11], 255)
        with self.assertRaises(ValueError):
            sidetone_report(56)


if __name__ == "__main__":
    unittest.main()
