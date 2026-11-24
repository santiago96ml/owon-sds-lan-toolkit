import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from owon_sds import (
    OwonError,
    OwonScope,
    parse_probe,
    parse_scale_to_seconds,
    parse_volts,
)


class OwonParserTests(unittest.TestCase):
    def test_unit_parsers(self):
        self.assertAlmostEqual(parse_scale_to_seconds("1.0ms"), 0.001)
        self.assertAlmostEqual(parse_scale_to_seconds("500us"), 0.0005)
        self.assertAlmostEqual(parse_scale_to_seconds("20ns"), 20e-9)
        self.assertAlmostEqual(parse_volts("400.0mv"), 0.4)
        self.assertAlmostEqual(parse_volts("12.5v"), 12.5)
        self.assertIsNone(parse_volts("?"))
        self.assertEqual(parse_probe("X10"), 10)
        self.assertEqual(parse_probe("x1"), 1)

    def test_binary_payload_decode_and_volts_conversion(self):
        # Build synthetic SPBS0Ae payload with 1 channel (;CH1), 4 signed int8 points
        samples = bytes([0, 10, 256 - 10, 127])  # [0, 10, -10, 127]
        words_i = struct.pack(
            "<10i",
            60,    # block_len
            0, 0, 0,
            4,     # whole_points
            4,     # num_points
            16,    # timebase_idx -> 1ms
            0,     # zero_point
            8,     # voltage_idx -> 1v
            1,     # atten_idx -> X10
        )
        words_f = struct.pack("<4f", 1.0, 1000.0, 1.0, 10.0)  # vpp_mv = 10.0 mV
        payload = b"SPBS0Ae000" + b"SERIAL0000000000" + b";CH1" + words_i + words_f + samples

        parsed = OwonScope.parse_payload(payload, flags=1)
        self.assertIn(1, parsed["channels"])
        ch1 = parsed["channels"][1]
        self.assertEqual(ch1["counts"], [0, 10, -10, 127])

        volts = OwonScope.to_volts(ch1)
        # vpc = (10 mV / 1000) * 10x probe = 0.1 V/count
        self.assertAlmostEqual(volts[0], 0.0)
        self.assertAlmostEqual(volts[1], 1.0)
        self.assertAlmostEqual(volts[2], -1.0)

        t_ms = OwonScope.time_axis_ms(ch1)
        # 10 div * 1 ms = 10 ms across 4 samples => dt = 2.5 ms
        self.assertEqual(len(t_ms), 4)
        self.assertAlmostEqual(t_ms[1] - t_ms[0], 2.5)

    def test_empty_payload_raises(self):
        with self.assertRaises(OwonError):
            OwonScope.parse_payload(b"INVALID")


if __name__ == "__main__":
    unittest.main()
