import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import os
import tempfile
import time
import unittest
from unittest import mock

import pc_health


class ReadingsTests(unittest.TestCase):
    def test_reads_this_pc(self):
        now = pc_health.readings()
        self.assertTrue(0 <= now["cpu"] <= 100)
        self.assertTrue(0 < now["memory"]["total_gb"] and 0 <= now["memory"]["percent"] <= 100)
        self.assertTrue(0 <= now["disk"]["free_gb"] <= now["disk"]["total_gb"])
        self.assertGreater(now["uptime"], 0)
        self.assertIn("CPU", pc_health.words(now))
        busy = pc_health.busiest(0.2)
        self.assertTrue(busy["memory"])
        self.assertIn("Using memory", pc_health.busiest_words(busy))

    def test_words(self):
        self.assertEqual(pc_health.battery_words({"percent": 42, "charging": False, "minutes_left": 95}),
                         "Battery 42%, about 1 h 35 min left.")
        self.assertEqual(pc_health.battery_words({"percent": 80, "charging": True, "minutes_left": None}), "Battery 80%, charging.")
        self.assertEqual(pc_health.battery_words(None), "This PC has no battery.")
        self.assertEqual(pc_health.size_words(3 * (1 << 20)), "3 MB")
        self.assertEqual(pc_health.app_name(r"C:\Program Files\Google\Chrome\chrome.exe"), "Chrome")
        self.assertEqual(pc_health.app_name(r"C:\x\notepad.exe"), "Notepad")


class HealthWatchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.alerts, self.switches = [], {}
        self.watch = pc_health.HealthWatch(lambda kind, text: self.alerts.append((kind, text)), lambda: self.switches,
                                           self.folder.name)

    def write(self, name, size):
        with open(os.path.join(self.folder.name, name), "wb") as f:
            f.write(b"x" * size)

    def test_low_battery_once_per_level(self):
        for percent in (35, 21, 20, 19, 15, 10, 9):
            self.watch.check_battery({"percent": percent, "charging": False, "minutes_left": 30})
        self.assertEqual([text for _, text in self.alerts], ["🔋 Battery at 20% (about 30 min left): plug in the charger.",
                                                             "🔋 Battery at 10% (about 30 min left): plug in the charger."])
        self.watch.check_battery({"percent": 12, "charging": True, "minutes_left": None})  # plugged in, then out again
        self.watch.check_battery({"percent": 12, "charging": False, "minutes_left": None})
        self.watch.check_battery({"percent": 10, "charging": False, "minutes_left": None})
        self.assertEqual([text for _, text in self.alerts[2:]], ["🔋 Battery at 12%: plug in the charger.",
                                                                 "🔋 Battery at 10%: plug in the charger."])
        self.switches["low_battery"] = False
        self.watch.check_battery({"percent": 50, "charging": True, "minutes_left": None})
        self.watch.check_battery({"percent": 20, "charging": False, "minutes_left": None})
        self.assertEqual(len(self.alerts), 4)

    def test_disk_once_a_day(self):
        full = {"drive": "C:", "free_gb": 3.2, "total_gb": 500, "percent": 99}
        self.watch.check_disk(full, now=1000)
        self.watch.check_disk(full, now=5000)
        self.watch.check_disk({"drive": "C:", "free_gb": 300, "total_gb": 500, "percent": 40}, now=200000)
        self.watch.check_disk(full, now=100000)
        self.assertEqual([kind for kind, _ in self.alerts], ["disk", "disk"])
        self.assertIn("C: is almost full: 3.2 GB left", self.alerts[0][1])

    def test_finished_downloads(self):
        self.write("old.pdf", 10)
        self.watch.check_downloads()  # what was there before isn't news
        self.write("movie.mp4.crdownload", 100)
        self.write("movie.mp4", 0)  # Firefox-style empty stand-in while it downloads
        self.write("desktop.ini", 5)
        self.watch.check_downloads()
        self.watch.check_downloads()
        self.assertEqual(self.alerts, [])
        os.remove(os.path.join(self.folder.name, "movie.mp4.crdownload"))
        self.write("movie.mp4", 3 * (1 << 20))
        self.watch.check_downloads()  # still growing, maybe
        self.assertEqual(self.alerts, [])
        self.watch.check_downloads()
        self.assertEqual(self.alerts, [("downloads", "⬇ Downloaded “movie.mp4” (3 MB).")])
        self.watch.check_downloads()
        self.assertEqual(len(self.alerts), 1)
        self.switches["downloads"] = False
        self.write("quiet.zip", 7)
        self.watch.check_downloads()
        self.watch.check_downloads()
        self.assertEqual(len(self.alerts), 1)


class FinishWatchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.said = []
        self.watch = pc_health.FinishWatch(self.said.append, self.folder.name)
        self.addCleanup(self.watch.stop)

    def test_a_download(self):
        partial = os.path.join(self.folder.name, "big.iso.crdownload")
        open(partial, "wb").close()
        with mock.patch("threading.Thread"):
            self.assertEqual(self.watch.start(), "Okay, I'll tell you when your download finishes.")
        job = self.watch._watching
        self.assertFalse(self.watch.step(job))
        os.remove(partial)
        self.assertTrue(self.watch.step(job))
        self.assertEqual(self.said, ["✅ Your download finished."])
        self.assertEqual(self.watch.what(), "")

    def test_the_window_in_front(self):
        with mock.patch.object(pc_health, "window_process", return_value=4242), \
                mock.patch.object(pc_health, "window_title", return_value="Copying 1,204 items"), \
                mock.patch.object(pc_health, "process_ticks", return_value=0), mock.patch("threading.Thread"):
            self.assertEqual(self.watch.start(foreground=77), "Okay, I'll tell you when “Copying 1,204 items” finishes.")
        job = self.watch._watching
        ticks = iter([4e6, 4e6, 4e6, 4e6])  # busy (20% of a core), then quiet
        with mock.patch.object(pc_health._user32, "IsWindow", return_value=True), \
                mock.patch.object(pc_health, "window_title", return_value="Copying 1,204 items"), \
                mock.patch.object(pc_health, "process_ticks", side_effect=lambda pid: next(ticks)):
            self.assertFalse(self.watch.step(job, now=job["started"] + 2))
            self.assertTrue(job["worked"])
            self.assertFalse(self.watch.step(job, now=job["started"] + 4))
            self.assertFalse(self.watch.step(job, now=job["started"] + 20))
            self.assertTrue(self.watch.step(job, now=job["started"] + 40))
        self.assertEqual(self.said, ["✅ “Copying 1,204 items” looks finished: its app went quiet."])

    def test_closing_or_saying_done(self):
        for still_open, title, said in ((False, "Setup", "✅ “Setup” closed: it's finished."),
                                        (True, "Setup completed", "✅ “Setup” says “Setup completed”.")):
            self.said.clear()
            with mock.patch.object(pc_health, "window_process", return_value=4242), \
                    mock.patch.object(pc_health, "window_title", return_value="Setup"), \
                    mock.patch.object(pc_health, "process_ticks", return_value=None), mock.patch("threading.Thread"):
                self.watch.start(foreground=77)
            with mock.patch.object(pc_health._user32, "IsWindow", return_value=still_open), \
                    mock.patch.object(pc_health, "window_title", return_value=title):
                self.assertTrue(self.watch.step(self.watch._watching))
            self.assertEqual(self.said, [said])

    def test_not_its_own_window_and_stop(self):
        with mock.patch.object(pc_health, "window_process", return_value=os.getpid()):
            self.assertIn("Bring the window", self.watch.start(foreground=77))
        self.assertEqual(self.watch.stop(), "I'm not watching anything.")
        with mock.patch.object(pc_health, "window_process", return_value=4242), \
                mock.patch.object(pc_health, "window_title", return_value="Render"), \
                mock.patch.object(pc_health, "process_ticks", return_value=None), mock.patch("threading.Thread"):
            self.watch.start(foreground=77)
        self.assertEqual(self.watch.stop(), "Okay, I stopped watching “Render”.")


if __name__ == "__main__":
    unittest.main()
