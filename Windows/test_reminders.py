import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import datetime as dt
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

import motorcycle_man as app
import reminders

MONDAY_2PM = dt.datetime(2026, 10, 19, 14, 0).timestamp()


def at(day, hour, minute=0):
    return dt.datetime(2026, 10, day, hour, minute).timestamp()


class ReminderWordsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.book = reminders.ReminderBook(lambda: os.path.join(self.temp.name, "reminders.json"))

    def said(self, text, now=MONDAY_2PM):
        return reminders.handle(text, self.book, now)

    def last(self):
        return max(self.book.upcoming(), key=lambda r: r["made"])

    def test_when_and_what(self):
        cases = [
            ("remind me in 20 minutes to stretch", MONDAY_2PM + 1200, "Stretch"),
            ("Remind me to call Dana at 6:30 pm", at(19, 18, 30), "Call Dana"),
            ("hey motorcycle man, remind me tomorrow to pay rent", at(20, 9), "Pay rent"),
            ("remind me at 5 to feed the cat", at(19, 17), "Feed the cat"),  # at 2 PM, "at 5" is this afternoon
            ("remind me at 1 to eat", at(20, 1), "Eat"),  # at 2 PM, 1 PM has passed too: 1 AM tomorrow
            ("set a reminder for 5pm to call mom", at(19, 17), "Call mom"),
            ("remind me friday at noon about lunch with Sam", at(23, 12), "Lunch with Sam"),
            ("remind me tonight at 8 to watch the race", at(19, 20), "Watch the race"),
            ("remind me in an hour and a half to check the oven", MONDAY_2PM + 5400, "Check the oven"),
            ("Remind me in 2 hours and 15 minutes to leave", MONDAY_2PM + 8100, "Leave"),
            ("remind me at 10 am tomorrow to call the bank", at(20, 10), "Call the bank"),
            ("remind me next monday at 7 pm to call grandma", at(26, 19), "Call grandma"),
            ("remind me this evening to water the plants", at(19, 19), "Water the plants"),
            ("remind me to work for 2 hours at 3pm", at(19, 15), "Work for 2 hours"),
            ("remind me in half an hour", MONDAY_2PM + 1800, "Your reminder"),
        ]
        for text, due, task in cases:
            with self.subTest(text):
                self.assertTrue(self.said(text).startswith("Okay, I'll remind you"), self.said)
                self.assertEqual((self.last()["due"], self.last()["text"]), (due, task))

    def test_the_reply_says_when(self):
        self.assertEqual(self.said("remind me in 20 minutes to stretch"), "Okay, I'll remind you in 20 minutes: Stretch")
        self.assertEqual(self.said("remind me at 6:30 pm to call Dana"), "Okay, I'll remind you at 6:30 PM: Call Dana")
        self.assertEqual(self.said("remind me tomorrow to pay rent"), "Okay, I'll remind you tomorrow at 9:00 AM: Pay rent")

    def test_timers(self):
        self.assertEqual(self.said("set a timer for 10 minutes"), "Timer set for 10 minutes.")
        self.assertEqual((self.last()["due"], self.last()["text"], self.last()["kind"]),
                         (MONDAY_2PM + 600, "Your 10-minute timer is done", "timer"))
        self.assertEqual(self.said("5 minute timer"), "Timer set for 5 minutes.")
        self.assertEqual(self.said("timer for an hour and a half"), "Timer set for 1 hour and 30 minutes.")

    def test_not_reminders_or_not_enough(self):
        for text in ("open notepad", "remind me what I did yesterday", "how long is left on my timer", ""):
            self.assertIsNone(self.said(text), text)
        self.assertTrue(self.said("remind me to stretch").startswith("When should I remind you?"))
        self.assertTrue(self.said("remind me at 25:00 to sleep").startswith("That isn't a time I know"))
        self.assertEqual(self.book.upcoming(), [])

    def test_listing_and_cancelling(self):
        self.assertTrue(self.said("what are my reminders").startswith("You have no reminders"))
        self.said("remind me at 6 pm to call Dana")
        self.said("remind me tomorrow to pay rent")
        self.said("set a timer for 5 minutes")
        listed = self.said("what reminders do i have")
        self.assertIn("6:00 PM: Call Dana", listed)
        self.assertIn("tomorrow at 9:00 AM: Pay rent", listed)
        self.assertEqual(self.said("cancel the reminder to call dana"), "Okay, I cancelled the reminder: Call Dana")
        self.assertEqual(self.said("cancel the reminder to call dana"), "There's no reminder like that to cancel.")
        self.assertEqual(self.said("stop the timer"), "Okay, I cancelled the timer: Your 5-minute timer is done")
        self.assertEqual([r["text"] for r in self.book.upcoming()], ["Pay rent"])
        self.said("remind me in 1 hour to stand up")
        self.assertEqual(self.said("clear all reminders"), "Okay, I cancelled 2 reminders.")

    def test_due_reminders_are_taken_once_and_very_late_ones_dropped(self):
        self.book.add("Long gone", MONDAY_2PM - reminders.LATE_LIMIT - 60)
        self.book.add("Just missed", MONDAY_2PM - 300)
        self.book.add("Later", MONDAY_2PM + 60)
        self.assertEqual([r["text"] for r in self.book.take_due(MONDAY_2PM)], ["Just missed"])
        self.assertEqual(self.book.take_due(MONDAY_2PM), [])
        self.assertEqual(self.book.next_due(), MONDAY_2PM + 60)


class RemindersInAppTests(unittest.TestCase):
    TOKEN = "reminder-test-token"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (mock.patch.dict(os.environ, {"APPDATA": self.temp.name}),
                        mock.patch.object(app, "PHONE_SESSION_TOKEN", self.TOKEN), mock.patch.dict(app.PHONE_CONTACT),
                        mock.patch.dict(app.TASK_STATUS), mock.patch.object(app.PC_VOICE, "say")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bridge = mock.Mock()

    def test_a_reminder_is_set_without_stopping_the_task_that_runs(self):
        with mock.patch.object(app, "_execute_smart_action_locked") as task:
            self.assertTrue(app.TASK_LOCK.acquire(blocking=False))  # a task is running
            try:
                reply = app.execute_smart_action("remind me in 10 minutes to stretch", self.bridge, 1000, 800)
            finally:
                app.TASK_LOCK.release()
        task.assert_not_called()
        self.assertEqual(reply, "Okay, I'll remind you in 10 minutes: Stretch")
        self.assertEqual([r["text"] for r in app.REMINDERS.upcoming()], ["Stretch"])
        self.assertEqual(app.load_history()[-1]["reply"], reply)

    def test_a_due_reminder_shows_everywhere(self):
        reminder = app.REMINDERS.add("Stretch", time.time() - 1)
        with mock.patch.object(app, "telegram_alert") as telegram:
            for item in app.REMINDERS.take_due(time.time()):
                app.fire_reminder(self.bridge, item)
        self.bridge.say_sig.emit.assert_called_once_with("⏰ Stretch", 20000)
        self.bridge.act_sig.emit.assert_called_once_with("alarm", {})
        app.PC_VOICE.say.assert_called_once_with("Reminder: Stretch")
        telegram.assert_called_once_with("⏰ Stretch")
        self.assertEqual(app.get_task_status()["notice"]["id"], reminder["id"])
        self.assertEqual(app.load_history()[-1]["reply"], "Stretch")
        app.save_setting("voice_replies", False)
        app.PC_VOICE.say.reset_mock()
        with mock.patch.object(app, "telegram_alert"):
            app.fire_reminder(self.bridge, dict(reminder, due=time.time() - 3600))
        app.PC_VOICE.say.assert_not_called()
        self.assertIn("(it was due", self.bridge.say_sig.emit.call_args.args[0])

    def test_the_clock_fires_a_reminder_when_it_is_due(self):
        fired, stopped = threading.Event(), threading.Event()
        with mock.patch.object(app, "fire_reminder", side_effect=lambda *args: fired.set()):
            clock = threading.Thread(target=app.reminder_clock, args=(self.bridge, stopped), daemon=True)
            clock.start()
            try:
                time.sleep(0.1)
                app.REMINDERS.add("Soon", time.time() + 0.3)
                self.assertTrue(fired.wait(3))
            finally:  # never left running on the real reminders once the stand-in folder is gone
                stopped.set()
                app.REMINDERS.changed.set()
                clock.join(3)
        self.assertFalse(clock.is_alive())

    def test_the_phone_lists_and_cancels_reminders(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(self.bridge, 1000, 800))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        first = app.REMINDERS.add("Call Dana", time.time() + 3600)
        app.REMINDERS.add("Pay rent", time.time() + 7200)
        listed = json.loads(opener.open(f"{base}/reminders?token={self.TOKEN}", timeout=5).read())["reminders"]
        self.assertEqual([r["text"] for r in listed], ["Call Dana", "Pay rent"])
        self.assertTrue(all(r["when"] for r in listed))
        left = json.loads(opener.open(f"{base}/reminders?cancel={first['id']}&token={self.TOKEN}", timeout=5).read())
        self.assertEqual([r["text"] for r in left["reminders"]], ["Pay rent"])
        for needed in ('id="reminderModal"', "function addReminder", "showNotice(state.notice)"):
            self.assertIn(needed, app.PHONE_PAGE)


if __name__ == "__main__":
    unittest.main()
