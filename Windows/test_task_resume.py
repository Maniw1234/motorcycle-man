import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.server
import itertools
import json
import os
import tempfile
import threading
import time
import types
import unittest
import urllib.parse
import urllib.request
from unittest import mock

import motorcycle_man as app
import task_journal


class FakeSignal:
    def __init__(self):
        self.values = []

    def emit(self, *values):
        self.values.append(values)


class FakeBridge:
    # Every signal the code emits keeps what it was given.
    def __getattr__(self, name):
        if not name.endswith("_sig"):
            raise AttributeError(name)
        signal = FakeSignal()
        setattr(self, name, signal)
        return signal


def plan(actions, message=None, done=False, **more):
    return dict({"actions": actions, "done": done}, **({"message": message} if message is not None else {}), **more)


SEND_BUTTON = (["[1] edit 'Message' at (400, 500)", "[2] button 'Send' at (600, 500)"], {1: (800, 1000), 2: (1200, 1000)})


class ResumeTestCase(unittest.TestCase):
    # A pretend PC: the planner, the screen and the actions are all stand-ins, and the journal lives in a
    # folder of the test's own.
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = os.path.join(self.temp.name, "Motorcycle Man")
        os.makedirs(folder)
        with open(os.path.join(folder, "keys.json"), "w", encoding="utf-8") as f:
            json.dump({"gemini_api_key": "unit-test-key"}, f)
        self.patch(mock.patch.dict(os.environ, {"APPDATA": self.temp.name}))
        self.patch(mock.patch.dict(app.RESTING_MODELS, clear=True))
        folders = {name: self.temp.name for name in ("Desktop", "Downloads", "Documents")}
        for name, value in (("get_known_folder_paths", folders), ("discover_installed_shortcuts", {}),
                            ("discover_start_apps", {}), ("list_open_windows", ["Notepad"]),
                            ("foreground_window_kind", None), ("window_to_work_on", None),
                            ("window_title", "Notepad"),  # not the real foreground window's title
                            ("screen_fingerprint", ("before", None)), ("screen_changed", True),
                            ("quick_command", None), ("task_sleep", None), ("focus_for_paste", None),
                            ("clipboard_sequence", 0), ("clipboard_text", ""), ("wake_screen", None),
                            ("telegram_alert", False), ("chat_alert", False), ("try_instant_local_command", None),
                            ("installed_apps", {})):
            self.patch(mock.patch.object(app, name, return_value=value))
        self.patch(mock.patch.object(app.time, "sleep"))
        self.patch(mock.patch.object(app, "transcribe_speech", side_effect=RuntimeError("offline")))
        self.patch(mock.patch.object(app, "research", side_effect=AssertionError("no research expected")))
        self.controls = ([], {})
        self.patch(mock.patch.object(app, "describe_window_controls", side_effect=lambda *a, **k: self.controls))
        self.same_screen, shots = False, itertools.count(1)
        self.patch(mock.patch.object(app, "capture_screen_for_planner", side_effect=lambda: (
            "same" if self.same_screen else f"screen-{next(shots)}", 1920, 1080, 960, 540)))
        self.gemini = self.patch(mock.patch.object(app, "call_gemini_planner"))
        self.run_action = self.patch(mock.patch.object(app, "execute_single_action",
                                                       side_effect=lambda act, *a, **k: "ran " + act["type"]))
        self.ask = self.patch(mock.patch.object(app, "ask_user_to_confirm"))
        app.PENDING_SEND.update(request="", time=0.0, task="")
        self.addCleanup(app.PENDING_SEND.update, request="", time=0.0, task="")
        self.bridge = FakeBridge()

    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def run_command(self, command, plans, confirm=()):
        self.gemini.side_effect = plans
        self.ask.side_effect = list(confirm)
        return app.execute_smart_action(command, self.bridge, 1920, 1080)

    def resume(self, plans, confirm=(), task_id=None):
        self.gemini.side_effect = plans
        self.ask.side_effect = list(confirm)
        return app.resume_task(self.bridge, 1920, 1080, task_id=task_id, source="phone")

    def prompt(self, call=-1) -> str:
        return self.gemini.call_args_list[call].args[0][-1]["text"]

    def stopped_mid_action(self, kind="press_key"):
        # A task whose second action is cut off halfway by the phone's Stop button.
        def act(action, *args, **kwargs):
            if action["type"] == kind:
                app.stop_task()
                app.stop_point()  # what a stoppable wait inside the action does
            return "ran " + action["type"]
        self.run_action.side_effect = act
        actions = [{"type": "open_app", "app": "Gmail"}, {"type": "type_text", "text": "hi Dana"},
                   {"type": "press_key", "key": "enter"}]
        reply = self.run_command("send Dana a hi on Gmail", [plan(actions, todo="press Enter to send")])
        self.run_action.side_effect = lambda action, *a, **k: "ran " + action["type"]
        self.gemini.reset_mock()
        return reply


class HaltTests(ResumeTestCase):
    def test_a_task_stopped_mid_action_is_paused_with_what_it_did(self):
        reply = self.stopped_mid_action()
        self.assertEqual(reply, "Okay, I stopped.")  # the reply is as it always was
        entry = app.TASKS.offer()
        self.assertEqual((entry["state"], entry["why"], entry["request"]), ("paused", "stop", "send Dana a hi on Gmail"))
        self.assertEqual(entry["done"], ["opening Gmail", "typing 7 characters"])  # not what was typed
        self.assertEqual(entry["unsure"], "pressing Enter")  # the action cut off may have happened
        self.assertEqual(entry["step"], 1)
        self.assertEqual(app.TASKS.for_ui(entry)["words"], "You said stop.")
        self.assertEqual(app.TASK_ENDED.state, "paused")

    def test_a_copy_doesnt_change_the_step_number_it_notes(self):
        # The copy note reuses its own name for the copied words; the journal still gets the step's number.
        calls = itertools.count()
        app.clipboard_sequence.side_effect = lambda: min(next(calls), 1)  # the first action copies something
        app.clipboard_text.return_value = "Order 1234"

        def act(action, *args, **kwargs):
            if action["type"] == "type_text":
                app.stop_task()
                app.stop_point()
            return "ran " + action["type"]
        self.run_action.side_effect = act
        self.run_command("copy the order number into Notepad", [plan([{"type": "hotkey", "keys": ["ctrl", "c"]},
                                                                     {"type": "type_text", "text": "Order 1234"}])])
        entry = app.TASKS.offer()
        self.assertEqual((entry["state"], entry["step"]), ("paused", 1))
        self.assertEqual((entry["done"], entry["unsure"]), (["pressing Ctrl+C"], "typing 10 characters"))

    def test_a_finished_task_leaves_nothing_to_pick_up(self):
        reply = self.run_command("find the newsletter from last week",
                                 [plan([{"type": "press_key", "key": "tab"}], "Found it.", done=True)])
        self.assertEqual(reply, "Found it.")
        self.assertIsNone(app.TASKS.offer())
        self.assertEqual(app.LIVE_TASK["id"], "")

    def test_a_screen_that_never_changes_is_stuck(self):
        self.same_screen = True
        scroll = {"type": "scroll", "direction": "down", "amount": 5, "x": 500, "y": 300}
        reply = self.run_command("find the newsletter", [plan([dict(scroll)]) for _ in range(6)])
        self.assertTrue(reply.startswith("Stopped: the screen didn't change after 3 tries."))
        entry = app.TASKS.offer()
        self.assertEqual((entry["state"], entry["why"]), ("stuck", "unchanged"))

    def test_a_resting_ai_pauses_the_task_until_it_may_be_asked_again(self):
        def busy(*args, **kwargs):
            for model in app.GEMINI_MODELS:  # what a 429 from each model leaves behind (see ask_gemini)
                app.RESTING_MODELS[model] = time.monotonic() + 600
            raise RuntimeError("Gemini (gemini-3.5-flash) answered HTTP 429. Quota exceeded.")
        reply = self.run_command("open Gmail and check my mail", busy)
        self.assertEqual(reply, "Stopped after 3 failed tries in a row. "
                                "Last problem: Gemini (gemini-3.5-flash) answered HTTP 429. Quota exceeded.")
        entry = app.TASKS.offer()
        self.assertEqual((entry["state"], entry["why"]), ("paused", "ai_busy"))
        self.assertGreater(entry["retry_at"], time.time() + 500)
        self.gemini.reset_mock()
        self.assertEqual(self.resume([]), "The AI is still resting. Try again in about 10 minutes.")
        self.gemini.assert_not_called()
        self.assertEqual(app.TASKS.offer()["state"], "paused")  # still there, and that didn't count as a try
        self.assertEqual(app.TASKS.offer()["resumes"], 0)

    def test_a_planner_that_keeps_failing_otherwise_is_stuck(self):
        reply = self.run_command("find the newsletter from last week",
                                 RuntimeError("Gemini (gemini-3.5-flash) didn't return a plan (empty)"))
        self.assertTrue(reply.startswith("Stopped after 3 failed tries in a row."))
        self.assertEqual((app.TASKS.offer()["state"], app.TASKS.offer()["why"]), ("stuck", "failed"))

    def test_a_declined_risky_action_drops_the_task(self):
        delete = {"type": "delete_file", "path": r"C:\Users\Test\old-notes.txt"}
        reply = self.run_command("clean up old notes", [plan([delete])], confirm=[False])
        self.assertEqual(reply, "Stopped: you didn't allow \"Delete C:\\Users\\Test\\old-notes.txt?\"")
        self.assertIsNone(app.TASKS.offer())

    def test_the_time_limit_pauses_it_and_the_next_go_has_its_own_limits(self):
        with mock.patch.object(app, "MAX_TASK_ACTIONS", 2):
            reply = self.run_command("tidy the desktop", [plan([{"type": "press_key", "key": "tab"}] * 2, todo="two more")] * 3)
            self.assertEqual(reply, "Stopped at Motorcycle Man's time or step limit. Done so far: ran press_key, ran press_key.")
            entry = app.TASKS.offer()
            self.assertEqual((entry["state"], entry["why"], len(entry["done"])), ("paused", "limit", 2))
            reply = self.resume([plan([{"type": "press_key", "key": "tab"}]), plan([], "All tidy.", done=True)])
        self.assertEqual(reply, "All tidy.")  # not stopped at once by the 2 actions done before
        self.assertIn("Status: Step 2 — analyzing screen with Gemini...", [v[0] for v in self.bridge.status_sig.values])

    def test_a_held_send_is_paused_waiting_for_a_yes(self):
        self.controls = SEND_BUTTON
        reply = self.run_command("reply hi to Dana", [plan([{"type": "click_element", "id": 1},
                                                            {"type": "type_text", "text": "hi"},
                                                            {"type": "click_element", "id": 2}])])
        self.assertEqual(reply, "Ready. Say yes to send it.")
        entry = app.TASKS.offer()
        self.assertEqual((entry["state"], entry["why"]), ("paused", "waiting_yes"))
        self.assertEqual(app.PENDING_SEND["task"], entry["id"])

    def test_an_error_leaves_it_stuck(self):
        with mock.patch.object(app, "read_window_controls", side_effect=ValueError("no controls")):
            reply = self.run_command("find the newsletter from last week", [])
        self.assertEqual(reply, "Error: no controls")
        self.assertEqual((app.TASKS.offer()["state"], app.TASKS.offer()["why"]), ("stuck", "error"))

    def test_a_guests_task_is_never_kept(self):
        app.GUEST_COMMAND.name = "Sam"
        try:
            self.stopped_mid_action()
        finally:
            app.GUEST_COMMAND.name = None
        self.assertIsNone(app.TASKS.offer())


class ResumeRunTests(ResumeTestCase):
    def test_a_resume_plans_again_from_a_fresh_screen_and_never_replays(self):
        self.stopped_mid_action()
        self.run_action.reset_mock()
        self.controls = SEND_BUTTON
        reply = self.resume([plan([{"type": "click_element", "id": 1}], todo="check it sent"),
                             plan([], "Sent Dana a hi.", done=True)], confirm=[True])
        self.assertEqual(reply, "Sent Dana a hi.")
        # Unsure whether Enter went, it asks before anything but looking around (a click may be a Send).
        self.ask.assert_called_once_with(mock.ANY, "Before I stopped, I was pressing Enter. It may have happened "
                                                   "already. Should I go on (clicking \u201cMessage\u201d)?")
        text = self.prompt(0)
        self.assertIn("User command: send Dana a hi on Gmail\nThis task was paused at step 1 (the user said stop).", text)
        self.assertIn("Already done: opening Gmail; typing 7 characters.", text)
        self.assertIn("Your last note of what was left: press Enter to send.", text)
        self.assertIn("The last action (pressing Enter) may or may not have happened; check the screen first.", text)
        self.assertNotIn("hi Dana", text)  # what was typed stays off the disk, so a resume doesn't see it either
        self.assertEqual([c.args[0]["type"] for c in self.run_action.call_args_list], ["click_element"])
        self.assertIn("Status: Step 2 — analyzing screen with Gemini...", [v[0] for v in self.bridge.status_sig.values])
        self.assertIsNone(app.TASKS.offer())  # done

    def test_a_resume_asks_before_the_first_thing_it_cant_undo(self):
        self.stopped_mid_action()
        self.run_action.reset_mock()
        reply = self.resume([plan([{"type": "press_key", "key": "tab"}, {"type": "press_key", "key": "enter"}]),
                             plan([{"type": "press_key", "key": "enter"}]), plan([], "Sent.", done=True)],
                            confirm=[True, True])
        self.assertEqual(reply, "Sent.")
        unsure = "Before I stopped, I was pressing Enter. It may have happened already. "
        self.assertEqual(self.ask.call_args_list, [mock.call(mock.ANY, unsure + "Should I go on (pressing Tab)?"),
                                                   mock.call(mock.ANY, unsure + "Should I send it now?")])
        # tab asks too (until the user says to go on, only looking around runs unasked); the send still asks, and
        # that yes clears the guard for the rest of this go
        self.assertEqual([c.args[0]["key"] for c in self.run_action.call_args_list], ["tab", "enter", "enter"])

    def test_a_no_to_that_question_drops_the_task(self):
        self.stopped_mid_action()
        self.run_action.reset_mock()
        reply = self.resume([plan([{"type": "press_key", "key": "enter"}])], confirm=[False])
        self.assertEqual(reply, "Okay, I won't send it.")
        self.run_action.assert_not_called()
        self.assertIsNone(app.TASKS.offer())

    def test_a_risky_action_gets_one_question_not_two(self):
        self.stopped_mid_action()
        delete = {"type": "delete_file", "path": r"C:\Users\Test\draft.txt"}
        self.resume([plan([delete]), plan([], "Done.", done=True)], confirm=[True])
        self.ask.assert_called_once_with(mock.ANY, "Before I stopped, I was pressing Enter. It may have happened already. "
                                                   r"Delete C:\Users\Test\draft.txt?")

    def test_a_held_send_after_a_cut_off_says_it_may_have_gone(self):
        self.stopped_mid_action()
        self.controls = SEND_BUTTON
        reply = self.resume([plan([{"type": "click_element", "id": 2}])])
        self.assertEqual(reply, "I may have sent it already before I stopped. Have a look, and say yes if I should send it.")
        self.assertEqual(app.TASKS.offer()["why"], "waiting_yes")

    def test_yes_send_it_finishes_the_held_send_once(self):
        self.controls = SEND_BUTTON
        self.run_command("reply hi to Dana", [plan([{"type": "type_text", "text": "hi"}, {"type": "click_element", "id": 2}])])
        task_id = app.TASKS.offer()["id"]
        self.assertEqual(app.TASKS.for_ui(app.TASKS.offer(), send_waiting=app.send_still_held(app.TASKS.offer()))["ask"],
                         "Yes, send it")
        self.run_action.reset_mock()
        reply = self.resume([plan([{"type": "click_element", "id": 2}], "Sent!")], task_id=task_id)
        self.assertEqual(reply, "Sent!")
        self.assertIn("The user has said yes: do the final send/post/run step now.", self.prompt())
        self.assertEqual(self.run_action.call_count, 1)
        self.assertIsNone(app.TASKS.offer())

    def test_a_task_is_picked_up_once_and_at_most_three_times(self):
        self.stopped_mid_action()
        task_id = app.TASKS.offer()["id"]
        for _ in range(3):
            app.TASKS.claim(task_id)
            app.TASKS.pause(task_id, "stuck", "failed")  # as if each go got stuck again (a Stop doesn't count)
        reply = self.resume([])
        self.assertEqual(reply, "I've picked this one up 3 times already. Try saying it a new way.")
        self.assertIsNone(app.TASKS.offer())
        self.assertEqual(self.resume([]), "There's nothing to pick up right now.")

    def test_while_a_task_runs_keep_going_doesnt_stop_it(self):
        self.assertTrue(app.TASK_LOCK.acquire(blocking=False))
        try:
            app.TASK_STOP.clear()
            self.assertEqual(app.execute_smart_action("keep going", self.bridge, 1920, 1080), "I'm still on it!")
            self.assertFalse(app.TASK_STOP.is_set())
        finally:
            app.TASK_LOCK.release()

    def test_words_said_while_a_task_runs_are_written_down_before_anything_stops(self):
        # The PC's microphone or the phone's hold: written down outside the task's lock, then handled like typed
        # words, so "keep going" leaves the running task alone, and "stop" still stops it.
        app.transcribe_speech.side_effect = None
        app.transcribe_speech.return_value = "keep going"
        self.assertTrue(app.TASK_LOCK.acquire(blocking=False))
        try:
            app.TASK_STOP.clear()
            self.assertEqual(app.execute_smart_action("", self.bridge, 1920, 1080, wav_bytes=b"\0" * 4000),
                             "I'm still on it!")
            self.assertFalse(app.TASK_STOP.is_set())
            app.transcribe_speech.return_value = "stop"
            with mock.patch.object(app, "STOP_WAIT_SECONDS", 0.1):
                self.assertEqual(app.execute_smart_action("", self.bridge, 1920, 1080, wav_bytes=b"\0" * 4000),
                                 "Stopping...")
            self.assertTrue(app.TASK_STOP.is_set())
        finally:
            app.TASK_STOP.clear()
            app.TASK_LOCK.release()
        self.assertEqual(app.transcribe_speech.call_count, 2)

    def test_a_resume_never_stops_a_task_that_just_began(self):
        self.stopped_mid_action()
        task_id = app.TASKS.offer()["id"]
        claim = app.TASKS.claim

        def claim_then_busy(*args, **kwargs):
            entry = claim(*args, **kwargs)
            app.TASK_LOCK.acquire()  # another command started just then
            return entry
        app.TASK_STOP.clear()
        try:
            with mock.patch.object(app.TASKS, "claim", side_effect=claim_then_busy):
                reply = self.resume([])
        finally:
            app.TASK_LOCK.release()
        self.assertEqual(reply, "I'm still on it!")
        self.assertFalse(app.TASK_STOP.is_set())  # the task that began goes on
        entry = app.TASKS.get(task_id)
        self.assertEqual((entry["state"], entry["resumes"]), ("paused", 0))  # put back as it was
        self.gemini.assert_not_called()


class GuardTests(ResumeTestCase):
    def test_a_resume_asks_before_a_click_it_cant_name(self):
        self.stopped_mid_action()
        self.run_action.reset_mock()
        scroll = {"type": "scroll", "direction": "down", "amount": 3, "x": 500, "y": 300}
        reply = self.resume([plan([scroll, {"type": "click", "x": 700, "y": 400}])], confirm=[False])
        self.assertEqual(reply, "Okay, I won't.")
        self.ask.assert_called_once_with(mock.ANY, "Before I stopped, I was pressing Enter. It may have happened "
                                                   "already. Should I go on (clicking on the screen)?")
        self.assertEqual([c.args[0]["type"] for c in self.run_action.call_args_list], ["scroll"])  # no yes needed
        self.assertIsNone(app.TASKS.offer())

    def test_what_cant_be_undone_isnt_done_again_unasked(self):
        calls = itertools.count()

        def planner(*args, **kwargs):
            if next(calls) == 0:
                return plan([{"type": "open_app", "app": "Gmail"}, {"type": "press_key", "key": "enter"}],
                            todo="check it went")
            app.stop_task()
            app.stop_point()  # stopped between steps: nothing was cut off halfway
        self.assertEqual(self.run_command("send Dana a hi on Gmail", planner), "Okay, I stopped.")
        entry = app.TASKS.offer()
        self.assertEqual((entry["unsure"], entry["sent"], entry["done"]),
                         ("", ["pressing Enter"], ["opening Gmail", "pressing Enter"]))
        self.run_action.reset_mock()
        reply = self.resume([plan([{"type": "press_key", "key": "enter"}])], confirm=[False])
        self.assertEqual(reply, "Okay, I won't send it.")
        self.ask.assert_called_once_with(mock.ANY, "Before I stopped, I already did this: pressing Enter. "
                                                   "Should I send it now?")
        self.assertIn("Already sent or not undoable: pressing Enter. Do NOT do these again unless the screen shows "
                      "they did not happen.", self.prompt())
        self.run_action.assert_not_called()

    def test_a_held_send_picked_up_after_a_restart_asks_again(self):
        self.controls = SEND_BUTTON
        held = [plan([{"type": "type_text", "text": "hi"}, {"type": "click_element", "id": 2}])]
        self.assertEqual(self.run_command("reply hi to Dana", held), "Ready. Say yes to send it.")
        app.PENDING_SEND.update(request="", time=0.0, task="")  # not kept over a restart
        self.run_action.reset_mock()
        reply = self.resume([plan([{"type": "press_key", "key": "enter"}])], confirm=[False])
        self.assertEqual(reply, "Okay, I won't send it.")
        self.ask.assert_called_once_with(mock.ANY, "Earlier I held back the send for your yes. Should I send it now?")
        self.assertIn("the user has NOT said yes, so hold the final send/post/run step again", self.prompt())
        self.run_action.assert_not_called()
        self.assertIsNone(app.TASKS.offer())
        # Planned again up to the Send button, it holds the send again rather than say it may have gone.
        self.run_command("reply hi to Dana", held)
        app.PENDING_SEND.update(request="", time=0.0, task="")
        self.assertEqual(self.resume([plan([{"type": "click_element", "id": 2}])]), "Ready. Say yes to send it.")
        self.assertEqual(app.TASKS.offer()["why"], "waiting_yes")

    def test_another_command_drops_a_held_send_and_its_task(self):
        self.controls = SEND_BUTTON
        self.run_command("reply hi to Dana", [plan([{"type": "type_text", "text": "hi"},
                                                    {"type": "click_element", "id": 2}])])
        held = app.TASKS.offer()["id"]
        self.assertEqual(self.run_command("tidy the desktop", [plan([], "All tidy.", done=True)]), "All tidy.")
        self.assertIsNone(app.TASKS.get(held))  # not offered later as a paused task
        self.assertEqual(app.PENDING_SEND["request"], "")

    def test_forget_it_drops_the_held_send_not_an_older_task(self):
        older = app.TASKS.start("open Notepad")
        app.TASKS.pause(older, "paused", "limit")
        end = time.monotonic() + 0.05
        while time.monotonic() < end:  # the held one is paused later (time.sleep is a stand-in here)
            pass
        self.controls = SEND_BUTTON
        self.run_command("reply hi to Dana", [plan([{"type": "type_text", "text": "hi"},
                                                    {"type": "click_element", "id": 2}])])
        held = app.PENDING_SEND["task"]
        self.assertEqual(app.execute_smart_action("forget it", self.bridge, 1920, 1080), "Okay, I won't send it.")
        self.assertIsNone(app.TASKS.get(held))
        self.assertEqual(app.TASKS.offer()["id"], older)

    def test_what_was_typed_never_reaches_the_disk(self):
        def act(action, *args, **kwargs):
            if action["type"] == "press_key":
                app.stop_task()
                app.stop_point()
            return {"type_text": f'Typed "{action.get("text")}"',
                    "search_web": f"Searched web for {action.get('query')}"}.get(action["type"], "ran " + action["type"])
        self.run_action.side_effect = act
        self.run_command("fill in the code from my texts",
                         [plan([{"type": "type_text", "text": "482913"},
                                {"type": "search_web", "query": "what is 482913"}], todo="press Enter"),
                          plan([{"type": "press_key", "key": "enter"}])])
        with open(os.path.join(self.temp.name, "Motorcycle Man", "tasks.json"), encoding="utf-8") as f:
            self.assertNotIn("482913", f.read())
        entry = app.TASKS.offer()
        self.assertEqual((entry["state"], entry["unsure"]), ("paused", "pressing Enter"))
        self.assertEqual(entry["done"], ["typing 6 characters", "searching the web"])
        self.assertEqual([h["result"] for h in entry["history"]], ['Typed "<6 chars>"', "Searched web for <14 chars>"])

    def test_forget_while_it_is_being_picked_up(self):
        self.stopped_mid_action()
        task_id = app.TASKS.offer()["id"]
        app.TASKS.claim(task_id)
        self.assertEqual(app.forget_task(task_id), "It's already starting. Say stop to stop it.")
        self.assertEqual(app.TASKS.get(task_id)["state"], "resuming")
        self.assertEqual(app.forget_task("nothere1"), "There's nothing to pick up right now.")

    def test_quit_keeps_the_running_task_to_pick_up(self):
        task_id = app.TASKS.start("send Dana the photos")
        app.TASKS.mark_in_flight(task_id, {"type": "press_key", "key": "enter"}, step=2)
        with mock.patch.object(app.Win32Input, "release_held_keys") as release:
            app.keep_task_on_quit()
        release.assert_called_once_with()
        entry = app.TASKS.get(task_id)
        self.assertEqual((entry["state"], entry["why"], entry["unsure"]), ("paused", "quit", "pressing Enter"))
        self.assertFalse(app.TASK_LOCK.locked())

    def test_telegram_and_chat_replies_say_how_to_pick_it_up(self):
        def ends(state, reply):
            def run(*args, **kwargs):
                app.TASK_ENDED.state = state
                return reply
            return run
        link = types.SimpleNamespace(bridge=self.bridge, sw=1920, sh=1080, label="WhatsApp")
        with mock.patch.object(app, "execute_smart_action", side_effect=ends("stuck", "Error: boom")):
            self.assertEqual(app.TelegramLink.run(link, "open Notepad"), "Error: boom Say \u201ckeep going\u201d to pick it up.")
            self.assertEqual(app.ChatService.run(link, "open Notepad"), "Error: boom Say \u201ckeep going\u201d to pick it up.")
        with mock.patch.object(app, "execute_smart_action", side_effect=ends("done", "Done!")):
            self.assertEqual(app.TelegramLink.run(link, "open Notepad"), "Done!")
        app.PENDING_SEND.update(request="reply hi to Dana", time=time.time(), task="a1b2c3d4")
        with mock.patch.object(app, "execute_smart_action", side_effect=ends("paused", "Ready. Say yes to send it.")):
            self.assertEqual(app.ChatService.run(link, "reply hi to Dana"), "Ready. Say yes to send it.")


class WordsTests(ResumeTestCase):
    def test_resume_and_forget_words(self):
        for text in ("keep going", "Keep going!", "okay keep going please", "carry on", "resume the task", "Resume task.",
                     "pick up where you left off", "finish my last task", "Motorcycle Man, keep going", "pick it back up"):
            with self.subTest(text=text):
                self.assertTrue(app.is_resume_request(text))
        for text in ("resume", "resume the music", "continue", "keep going down the page", "pick up the phone",
                     "finish my email", "find my resume", ""):
            with self.subTest(text=text):
                self.assertFalse(app.is_resume_request(text))
        for text in ("forget it", "never mind", "forget the paused task", "don't resume it"):
            self.assertTrue(app.is_forget_request(text), text)
        for text in ("forget my password", "stop"):
            self.assertFalse(app.is_forget_request(text), text)

    def test_typing_keep_going_picks_it_up(self):
        self.stopped_mid_action()
        self.gemini.side_effect = [plan([], "All done.", done=True)]
        self.assertEqual(app.execute_smart_action("keep going", self.bridge, 1920, 1080), "All done.")
        self.assertIn("This task was paused at step 1", self.prompt())

    def test_forget_it_drops_a_paused_task_and_otherwise_is_a_stop(self):
        self.assertEqual(app.execute_smart_action("forget it", self.bridge, 1920, 1080), "Okay. Nothing's running right now.")
        self.stopped_mid_action()
        self.assertEqual(app.execute_smart_action("forget it", self.bridge, 1920, 1080), "Okay, forgotten.")
        self.assertIsNone(app.TASKS.offer())
        self.assertEqual(app.execute_smart_action("forget the paused task", self.bridge, 1920, 1080),
                         "There's nothing to pick up right now.")

    def test_a_stop_said_into_the_microphone_is_noted_as_a_stop(self):
        task_id = app.TASKS.start("open Gmail")
        app.TASKS.pause(task_id, "paused", "newer")
        heard = {"stopped_one": True, "stopped_task": task_id}
        self.assertEqual(app._execute_smart_action_locked("stop", self.bridge, 1920, 1080, heard=heard), "Okay, I stopped.")
        self.assertEqual(app.TASKS.offer()["why"], "stop")

    def test_an_empty_push_to_talk_doesnt_stop_the_running_task(self):
        self.assertTrue(app.TASK_LOCK.acquire(blocking=False))
        try:
            app.TASK_STOP.clear()
            reply = app.execute_smart_action("", self.bridge, 1920, 1080, wav_bytes=b"")
            self.assertFalse(app.TASK_STOP.is_set())
        finally:
            app.TASK_LOCK.release()
        self.assertEqual(reply, "I didn't catch that. Tap the helmet and speak again, or tap Type.")


class LifecycleTests(ResumeTestCase):
    def test_a_restart_pauses_a_running_task_and_offers_it_once(self):
        task_id = app.TASKS.start("send Dana the photos")
        app.TASKS.mark_in_flight(task_id, {"type": "press_key", "key": "enter"}, step=3)
        journal = task_journal.TaskJournal(app.TASKS.path)  # what the next start reads
        with mock.patch.object(app, "TASKS", journal):
            journal.recover_after_restart(["Motorcycle Man.exe", "--tray", "--restarted"])
            app.offer_paused_task(self.bridge)
            app.offer_paused_task(self.bridge)
            entry = journal.offer()
        self.assertEqual((entry["state"], entry["why"], entry["unsure"]), ("paused", "crash", "pressing Enter"))
        self.assertEqual(self.bridge.notice_sig.values, [(
            "I was in the middle of \u201csend Dana the photos\u201d when I crashed and restarted. "
            "Say \u201ckeep going\u201d or tap Resume task.", 12000)])
        app.telegram_alert.assert_called_once()

    def test_a_stop_or_a_newer_command_isnt_offered_at_start(self):
        app.TASKS.pause(app.TASKS.start("open Gmail"), "paused", "newer")
        app.offer_paused_task(self.bridge)
        self.assertEqual(self.bridge.notice_sig.values, [])

    def test_an_update_stops_the_task_and_keeps_it_to_pick_up(self):
        task_id = app.TASKS.start("send Dana the photos")
        order = []
        with mock.patch.dict(app.UPDATE, {"release": {"version": "1.1"}, "busy": False}), \
                mock.patch.object(app.app_update, "download_update", return_value="new.exe"), \
                mock.patch.object(app, "stop_task", side_effect=lambda why: order.append(("stop", why))), \
                mock.patch.object(app.app_update, "start_install",
                                  side_effect=lambda *a: order.append(("install", app.TASKS.get(task_id)["why"]))):
            app.install_update(self.bridge)
        # Noted as cut off by the update only once the installer has started.
        self.assertEqual(order, [("stop", "update"), ("install", "")])
        self.assertEqual((app.TASKS.get(task_id)["state"], app.TASKS.get(task_id)["why"]), ("paused", "update"))
        self.assertFalse(app.TASK_LOCK.locked())
        self.assertEqual(len(self.bridge.update_quit_sig.values), 1)
        self.assertFalse(app.TASKS.checkpoint(task_id, step=9))  # the stopped thread's late note doesn't bring it back

    def test_the_phone_forgets_the_offered_task_without_an_id(self):
        token = "resume-test-token"
        self.patch(mock.patch.object(app, "PHONE_SESSION_TOKEN", token))
        self.patch(mock.patch.dict(app.PHONE_CONTACT))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(self.bridge, 1000, 800))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f"http://127.0.0.1:{server.server_address[1]}"
        app.TASKS.pause(app.TASKS.start("open Gmail"), "paused", "stop")
        answer = json.loads(opener.open(f"{base}/forget_task?token={token}", timeout=5).read())
        self.assertEqual(answer, {"ok": True, "reply": "Okay, forgotten."})
        self.assertIsNone(app.TASKS.offer())


class RoutineResumeTests(ResumeTestCase):
    def test_a_routine_stopped_at_a_step_goes_on_from_there(self):
        ran = []

        def task(cmd, bridge, sw, sh, wav_bytes, heard, resume=None):
            ran.append((cmd, resume is not None))
            if cmd == "post my status" and resume is None:
                heard["task"] = app.TASKS.start(cmd, "routine", app.ROUTINE_RUN.where)
                heard["ending"] = {"state": "paused", "why": "waiting_yes"}
                return "Your status is ready to post."  # the planner's own words: not a reply that ends a routine
            return f"Did: {cmd}"
        self.patch(mock.patch.object(app, "_execute_smart_action_locked", side_effect=task))
        app.ROUTINES.save("Morning", ["open gmail", "post my status", "open the weather"])
        self.assertEqual(app.execute_smart_action("morning", self.bridge, 1920, 1080),
                         "Morning stopped after 1 of 3: Your status is ready to post.")
        self.assertEqual(app.TASKS.offer()["routine"]["at"], 1)
        self.assertEqual(self.resume([]), "Morning: all done.")
        self.assertEqual(ran, [("open gmail", False), ("post my status", False), ("post my status", True),
                               ("open the weather", False)])

    def test_a_routine_changed_since_isnt_picked_up(self):
        routine = app.ROUTINES.save("Morning", ["open gmail", "post my status"])
        app.TASKS.pause(app.TASKS.start("post my status", "routine",
                                        {"id": routine["id"], "name": "Morning", "steps": routine["steps"], "at": 1}),
                        "paused", "stop")
        app.ROUTINES.save("Morning", ["open gmail", "open the weather"])
        self.assertEqual(self.resume([]), "That routine changed since then. Say its name to run it again.")
        self.assertIsNone(app.TASKS.offer())

    def test_only_motorcycle_mans_own_halt_replies_end_a_routine(self):
        for reply in ("Okay, I stopped.", "Stopped for your new command.", "Skipped for your newer command.",
                      "I'm still stopping the last task. Try again in a moment.", "Error: boom",
                      f"Stopped after {app.MAX_FAILED_STEPS} failed tries in a row. Last problem: busy",
                      "Stopped: the screen didn't change after 3 tries. ", 'Stopped: you didn\'t allow "Delete x?"',
                      'Stopped: no answer in time for "Delete x?"',
                      "Stopped at Motorcycle Man's time or step limit. Nothing was done.",
                      "Okay, I won't send it.", "Okay, I won't.", "Ready. Say yes to send it.",
                      "I may have sent it already before I stopped. Have a look, and say yes if I should send it."):
            self.assertTrue(reply.startswith(app.routines.STOPPED), reply)
        for reply in ("Stopped the music.", "Stopping the timer now.", "Your status is ready to post."):
            self.assertFalse(reply.startswith(app.routines.STOPPED), reply)

    def test_the_ais_own_stopped_doesnt_end_a_routine(self):
        ran = []

        def task(cmd, bridge, sw, sh, wav_bytes, heard, resume=None):
            ran.append(cmd)
            return "Stopped the music." if cmd == "pause the music" else f"Did: {cmd}"
        self.patch(mock.patch.object(app, "_execute_smart_action_locked", side_effect=task))
        app.ROUTINES.save("Evening", ["pause the music", "open the weather"])
        self.assertEqual(app.execute_smart_action("evening", self.bridge, 1920, 1080), "Evening: all done.")
        self.assertEqual(ran, ["pause the music", "open the weather"])


if __name__ == "__main__":
    unittest.main()
