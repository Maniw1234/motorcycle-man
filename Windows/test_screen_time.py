import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import os
import tempfile
import time
import unittest

import screen_time

NOW = time.mktime((2026, 10, 20, 15, 0, 0, 0, 0, -1))


class ScreenTimeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "screen_time.json")
        self.book = screen_time.ScreenTime(lambda: self.path)

    def test_a_day_app_by_app_and_the_week(self):
        for _ in range(12):
            self.book.add("Chrome", 300, r"C:\Apps\chrome.exe", stamp=NOW)
        self.book.add("Word", 1500, stamp=NOW)
        self.book.add("Chrome", 7200, stamp=NOW - 86400 * 2)
        self.assertEqual(self.book.day(screen_time.day_of(NOW)), [("Chrome", 3600), ("Word", 1500)])
        week = self.book.week(NOW)
        self.assertEqual([seconds for _, seconds in week], [0, 0, 0, 0, 7200, 0, 5100])
        self.assertEqual(self.book.path_of("Chrome"), r"C:\Apps\chrome.exe")
        self.assertEqual(screen_time.words(self.book.day(screen_time.day_of(NOW))),
                         "1 h 25 min on the PC today: Chrome 1 h, Word 25 min.")

    def test_kept_two_weeks(self):
        self.book.add("Chrome", 60, stamp=NOW - 86400 * 20)
        self.book.add("Chrome", 60, stamp=NOW)
        self.book.save(now=NOW)
        again = screen_time.ScreenTime(lambda: self.path)
        self.assertEqual(again.day(screen_time.day_of(NOW - 86400 * 20)), [])
        self.assertEqual(again.day(screen_time.day_of(NOW)), [("Chrome", 60)])
        self.assertEqual(screen_time.words([]), "Hardly any screen time today yet.")


if __name__ == "__main__":
    unittest.main()
