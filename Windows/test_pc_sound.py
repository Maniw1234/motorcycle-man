import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import unittest
from array import array

import pc_sound


def floats(data: bytes) -> list:
    out = array("f")
    out.frombytes(data)
    return [round(v, 4) for v in out]


class ToStereoTests(unittest.TestCase):
    def test_stereo_averaged_in_pairs(self):
        raw = array("f", [0.1, -0.1, 0.3, -0.3, 0.5, -0.5, 0.7, -0.7]).tobytes()  # four frames, left and right
        self.assertEqual(floats(pc_sound.to_stereo(raw, 2, 2)), [0.2, -0.2, 0.6, -0.6])

    def test_mono_and_surround_become_left_and_right(self):
        self.assertEqual(floats(pc_sound.to_stereo(array("f", [0.5, 0.25]).tobytes(), 1, 1)), [0.5, 0.5, 0.25, 0.25])
        six = array("f", [0.1, 0.2, 9, 9, 9, 9] * 2).tobytes()  # 5.1: only the front left and right are kept
        self.assertEqual(floats(pc_sound.to_stereo(six, 6, 1)), [0.1, 0.2, 0.1, 0.2])

    def test_bits_left_over_wait_for_more(self):
        self.assertEqual(pc_sound.to_stereo(array("f", [0.1, 0.2, 0.3]).tobytes(), 2, 2), b"")
        self.assertEqual(pc_sound.to_stereo(b"\x00\x00", 2, 1), b"")


if __name__ == "__main__":
    unittest.main()
