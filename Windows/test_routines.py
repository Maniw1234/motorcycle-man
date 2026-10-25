import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import datetime as dt
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from unittest import mock

import motorcycle_man as app
import reminders
import routines

MONDAY_2PM = dt.datetime(2026, 10, 19, 14, 0).timestamp()


class RoutineWordsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.book = routines.RoutineBook(lambda: os.path.join(self.temp.name, "routines.json"))
        self.schedules = reminders.ReminderBook(lambda: os.path.join(self.temp.name, "reminders.json"))

    def said(self, text):
        return routines.handle(text, self.book, self.schedules, MONDAY_2PM)

    def test_making_running_listing_and_deleting(self):
        kind, reply = self.said("make a routine called good morning: open gmail, then open the weather")
        self.assertEqual(kind, "reply")
        self.assertIn('"Good morning"', reply)
        kind, routine = self.said("good morning")
        self.assertEqual((kind, routine["steps"]), ("run", ["open gmail", "open the weather"]))
        for text in ("run good morning", "start my good morning routine", "Good Morning!"):
            self.assertEqual(self.said(text)[1]["id"], routine["id"], text)
        self.assertIn("Good morning: open gmail, then open the weather", self.said("what are my routines")[1])
        self.said("make a routine called good morning: open Slack")  # the same name: new steps
        self.assertEqual([r["steps"] for r in self.book.all()], [["open Slack"]])
        self.assertEqual(self.said("delete the good morning routine"), ("reply", 'Deleted the routine "Good morning".'))
        self.assertEqual(self.book.all(), [])
        self.assertTrue(self.said("what are my routines")[1].startswith("You have no routines"))

    def test_steps_read_like_commands(self):
        self.said("create a routine named Work that opens slack, then checks my email; plays lo-fi music")
        self.assertEqual(self.book.find("work")["steps"], ["open slack", "check my email", "play lo-fi music"])

    def test_schedules(self):
        self.said("create a routine named Work that opens slack and outlook every weekday at 8:30 am")
        work = self.book.find("work")
        self.assertEqual(work["steps"], ["open slack and outlook"])
        self.assertEqual(routines.schedule_of(work, self.schedules), {"days": [0, 1, 2, 3, 4], "at": "08:30"})
        entry = self.schedules.upcoming()[0]
        self.assertEqual((entry["kind"], entry["routine"], entry["due"]),
                         ("routine", work["id"], dt.datetime(2026, 10, 20, 8, 30).timestamp()))
        self.assertEqual(self.said("schedule my work routine every monday and wednesday at 9"),
                         ("reply", '"Work" will run every Monday and Wednesday at 9:00 AM.'))
        self.assertEqual(len(self.schedules.upcoming()), 1)  # the new schedule replaced the old one
        self.assertEqual(self.said("unschedule work"), ("reply", "\"Work\" won't run on its own any more."))
        self.assertEqual(self.schedules.upcoming(), [])

    def test_a_scheduled_routine_comes_round_again_but_not_hours_late(self):
        routine = self.book.save("Morning", ["open gmail"])
        routines.schedule(routine, {"days": list(range(7)), "at": "08:00"}, self.schedules, MONDAY_2PM)
        due = self.schedules.upcoming()[0]["due"]
        self.assertEqual([r["text"] for r in self.schedules.take_due(due + 60)], ["Morning"])  # a minute late: runs
        again = self.schedules.upcoming()[0]["due"]
        self.assertEqual(again, due + 86400)  # and it is due again tomorrow
        self.assertEqual(self.schedules.take_due(again + 3 * 3600), [])  # the PC was off: three hours late is too late
        self.assertEqual(self.schedules.upcoming()[0]["due"], again + 86400)

    def test_not_about_routines(self):
        self.book.save("Morning", ["open gmail"])
        for text in ("open notepad", "delete the report file", "run the tests", "remind me every day at 9 to read", ""):
            self.assertIsNone(self.said(text), text)


class RoutinesInAppTests(unittest.TestCase):
    TOKEN = "routine-test-token"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (mock.patch.dict(os.environ, {"APPDATA": self.temp.name}),
                        mock.patch.object(app, "PHONE_SESSION_TOKEN", self.TOKEN), mock.patch.dict(app.PHONE_CONTACT),
                        mock.patch.dict(app.TASK_STATUS)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bridge = mock.Mock()
        self.ran = []
        patcher = mock.patch.object(app, "_execute_smart_action_locked",
                                    side_effect=lambda cmd, *args, **kwargs: self.ran.append(cmd) or self.replies.get(cmd, f"Did: {cmd}"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.replies = {}

    def test_saying_its_name_runs_each_command_in_turn(self):
        app.ROUTINES.save("Good morning", ["open gmail", "open the weather"])
        reply = app.execute_smart_action("good morning", self.bridge, 1000, 800)
        self.assertEqual(self.ran, ["open gmail", "open the weather"])
        self.assertEqual(reply, "Good morning: all done.")
        self.assertEqual([e["request"] for e in app.load_history()], ["open gmail", "open the weather", "▶ Good morning"])

    def test_a_routine_stops_at_a_step_that_fails_and_never_runs_itself(self):
        app.ROUTINES.save("Loop", ["open gmail", "loop", "open the weather"])
        self.replies["loop"] = "Error: no such thing"
        reply = app.execute_smart_action("run loop", self.bridge, 1000, 800)
        self.assertEqual(self.ran, ["open gmail", "loop"])  # "loop" inside the routine went to Gemini, not round again
        self.assertEqual(reply, "Loop stopped after 1 of 3: Error: no such thing")

    def test_a_scheduled_routine_runs_when_due_and_tells_telegram(self):
        routine = app.ROUTINES.save("Morning", ["open gmail"])
        app.routines.schedule(routine, {"days": list(range(7)), "at": "08:00"}, app.REMINDERS)
        entry = app.REMINDERS.upcoming()[0]
        with mock.patch.object(app, "telegram_alert") as telegram:
            app.fire_reminder(self.bridge, entry, 1000, 800)
            deadline = time.monotonic() + 3
            while not telegram.called and time.monotonic() < deadline:
                time.sleep(0.02)
        self.assertEqual(self.ran, ["open gmail"])
        telegram.assert_called_once_with("▶ Your routine Morning: all done.")
        app.ROUTINES.delete(routine["id"])
        app.fire_reminder(self.bridge, entry, 1000, 800)  # its routine is gone: so is its schedule
        self.assertEqual(app.REMINDERS.upcoming(), [])

    def test_the_phone_makes_runs_and_deletes_routines(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(self.bridge, 1000, 800))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def call(**params):
            query = urllib.parse.urlencode(dict(params, token=self.TOKEN))
            return json.loads(opener.open(f"http://127.0.0.1:{server.server_address[1]}/routines?{query}", timeout=5).read())

        self.assertEqual(call(), {"routines": [], "say": ""})
        saved = call(save="1", name="Good night", steps="close Chrome\n\nlock the PC\n", repeat="weekdays", at="22:30")
        self.assertEqual(saved["say"], "Saved Good night.")
        self.assertEqual({k: saved["routines"][0][k] for k in ("name", "steps", "repeat", "at", "schedule")},
                         {"name": "Good night", "steps": ["close Chrome", "lock the PC"], "repeat": "weekdays", "at": "22:30",
                          "schedule": "every weekday at 10:30 PM"})
        self.assertEqual(call(save="1", name="", steps="x")["say"], "Give it a name and at least one command.")
        routine_id = saved["routines"][0]["id"]
        self.assertEqual(call(run=routine_id)["say"], "Running Good night...")
        deadline = time.monotonic() + 3
        while len(self.ran) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.ran, ["close Chrome", "lock the PC"])
        self.assertEqual(call(delete=routine_id), {"routines": [], "say": "Deleted Good night."})
        self.assertEqual(app.REMINDERS.upcoming(), [])  # its schedule went with it
        self.assertIn('id="routineModal"', app.PHONE_PAGE)


if __name__ == "__main__":
    unittest.main()
