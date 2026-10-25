import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import time
import unittest
from unittest import mock

import pc_power

NOW = time.mktime((2026, 10, 20, 14, 0, 0, 0, 0, -1))  # 2 PM


def minutes(asked):
    return asked and (asked[0], asked[1] and round((asked[1] - NOW) / 60))


class UnderstandTests(unittest.TestCase):
    def test_power_commands(self):
        for said, meant in [("shut down in 30 minutes", ("shutdown", 30)), ("please restart the PC in an hour", ("restart", 60)),
                            ("turn off the laptop at 11 pm", ("shutdown", 540)), ("put the computer to sleep in 20 min", ("sleep", 20)),
                            ("sleep in 5 minutes", ("sleep", 5)), ("lock the pc in 10 minutes", ("lock", 10)),
                            ("go to sleep in 10 minutes", ("sleep", 10)), ("shut down tomorrow at 7am", ("shutdown", 1020)),
                            ("shut down", ("shutdown", 1)), ("reboot", ("restart", 1)), ("turn off the pc", ("shutdown", 1)),
                            ("cancel shutdown", ("cancel", None)), ("Don’t shut down the PC", ("cancel", None)),
                            ("stop the restart timer", ("cancel", None)), ("shut down in banana", ("shutdown", None))]:
            self.assertEqual(minutes(pc_power.understand(said, NOW)), meant, said)

    def test_other_things_are_left_alone(self):
        for said in ("turn off", "turn off wifi", "restart chrome", "sleep", "lock", "lock screen", "sleep pc", "go to sleep",
                     "put to sleep", "remind me to shut down in 10 minutes", "lock the door"):
            self.assertIsNone(pc_power.understand(said, NOW), said)


class PowerTimerTests(unittest.TestCase):
    def setUp(self):
        self.done, self.warned = [], []
        self.timer = pc_power.PowerTimer(self.done.append, self.warned.append)
        self.addCleanup(self.timer.cancel)

    def wait_for(self, check):
        end = time.monotonic() + 3
        while not check() and time.monotonic() < end:
            time.sleep(0.01)
        return check()

    def test_warns_then_does_it(self):
        with mock.patch.object(pc_power, "WARN_BEFORE", 0.1):
            said = self.timer.set("restart", time.time() + 0.3)
            self.assertIn('Restarting in 1 minute. Say "cancel restart"', said)
            self.assertTrue(self.wait_for(lambda: self.done))
        self.assertEqual(self.done, ["restart"])
        self.assertEqual(len(self.warned), 1)
        self.assertIn("save your work", self.warned[0])
        self.assertEqual(self.timer.status()["action"], None)

    def test_a_short_wait_warns_at_once_and_cancel_stops_it(self):
        self.timer.set("shutdown", time.time() + 30)
        self.assertEqual(len(self.warned), 1)
        self.assertEqual(self.timer.status()["action"], "shutdown")
        self.assertEqual(self.timer.cancel(), "Okay, the PC won't shut down.")
        self.assertEqual(self.timer.cancel(), "There's no power timer set.")
        time.sleep(0.05)
        self.assertEqual(self.done, [])

    def test_a_new_one_replaces_the_old_and_sleep_is_quiet(self):
        self.timer.set("shutdown", time.time() + 0.2)
        self.timer.set("lock", time.time() + 0.3)
        self.assertTrue(self.wait_for(lambda: self.done))
        time.sleep(0.1)
        self.assertEqual(self.done, ["lock"])
        self.assertEqual(len(self.warned), 1)  # only the shutdown's
        self.assertIn("up to a day", self.timer.set("sleep", time.time() + 2 * 86400))

    def test_words(self):
        self.timer.set("sleep", NOW + 2 * 3600, now=NOW)
        self.assertEqual(self.timer.words(NOW), "Going to sleep at 4:00 PM.")
        self.assertEqual(self.timer.words(NOW + 3600 + 600), "Going to sleep in 50 minutes.")


class WakeInfoTests(unittest.TestCase):
    CARDS = [("00:00:00:00:00:00", "0.0.0.0", "0.0.0.0", "Bluetooth"),
             ("44:38:E8:00:00:01", "192.168.1.20", "255.255.255.0", "Intel(R) Wi-Fi 7 BE200 320MHz")]

    def test_only_on_the_same_network(self):
        with mock.patch.object(pc_power, "network_cards", return_value=self.CARDS), \
                mock.patch.object(pc_power, "cards_that_wake", return_value=["Intel(R) Wi-Fi 7 BE200 320MHz"]):
            self.assertEqual(pc_power.wake_info("192.168.1.20"), {"mac": "44:38:E8:00:00:01", "broadcast": "192.168.1.255",
                                                                    "card": "Intel(R) Wi-Fi 7 BE200 320MHz", "can_wake": True})
            for away in ("127.0.0.1", "8.8.8.8", "169.254.1.2", "nonsense", "192.168.5.200"):
                self.assertEqual(pc_power.wake_info(away), {}, away)
        with mock.patch.object(pc_power, "network_cards", return_value=self.CARDS), \
                mock.patch.object(pc_power, "cards_that_wake", return_value=None):
            self.assertIsNone(pc_power.wake_info("192.168.1.20")["can_wake"])

    def test_reads_this_pcs_cards(self):
        cards = pc_power.network_cards()
        self.assertIsInstance(cards, list)
        for mac, address, mask, name in cards:
            self.assertRegex(address, r"^\d+\.\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
