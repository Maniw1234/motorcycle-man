import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.server
import io
import json
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from unittest import mock
from urllib.error import HTTPError

import helmet_face
import bike_rider
import motorcycle_man as app


class FakeSignal:
    def __init__(self):
        self.values = []

    def emit(self, *values):
        self.values.append(values)


class FakeBridge:
    def __init__(self):
        self.hide_sig = FakeSignal()
        self.screen_hide_sig = FakeSignal()
        self.screen_show_sig = FakeSignal()
        self.status_sig = FakeSignal()
        self.pointer_sig = FakeSignal()
        self.say_sig = FakeSignal()
        self.notice_sig = FakeSignal()
        self.confirm_sig = FakeSignal()
        self.confirm_done_sig = FakeSignal()
        self.research_start_sig = FakeSignal()
        self.research_step_sig = FakeSignal()
        self.research_done_sig = FakeSignal()
        self.research_hide_sig = FakeSignal()
        self.mood_sig = FakeSignal()
        self.pair_sig = FakeSignal()
        self.pair_done_sig = FakeSignal()
        self.act_sig = FakeSignal()


def plan(actions, message=None, done=False):
    result = {"actions": actions, "done": done}
    if message is not None:
        result["message"] = message
    return result


# One valid example of every action type, for a 960x540 screenshot.
VALID_ACTIONS = [
    {"type": "open_app", "app": "notepad"},
    {"type": "open_url", "url": "https://example.com"},
    {"type": "open_url", "url": "example.com"},
    {"type": "search_web", "query": "weather"},
    {"type": "open_path", "path": r"C:\Users\Test\Documents"},
    {"type": "click_element", "id": 2, "clicks": 2, "say": "Opening the email"},
    {"type": "point_at", "id": 1, "say": "Projects is right here"},
    {"type": "point_at", "x": 300, "y": 200},
    {"type": "research", "question": "What is new in Claude's Projects feature?", "say": "Researching Projects"},
    {"type": "click", "x": 10, "y": 20, "button": "right", "clicks": 2},
    {"type": "double_click", "x": 959, "y": 539},
    {"type": "right_click", "x": 0, "y": 0},
    {"type": "move_mouse", "x": 100.5, "y": 200, "duration": 0.2},
    {"type": "mouse_down", "button": "left"},
    {"type": "mouse_up", "x": 5, "y": 5},
    {"type": "drag", "start_x": 1, "start_y": 2, "end_x": 300, "end_y": 400, "duration": 0.4},
    {"type": "scroll", "amount": -5, "x": 500, "y": 300, "horizontal": False},
    {"type": "type_text", "text": "h\u00e9llo \U0001F44B", "clear_first": True, "press_enter": True},
    {"type": "press_key", "key": "Enter", "presses": 2},
    {"type": "hotkey", "keys": ["ctrl", "c"]},
    {"type": "hotkey", "keys": "alt+tab"},
    {"type": "key_down", "key": "shift"},
    {"type": "key_up", "key": "shift"},
    {"type": "focus_window", "title": "Notepad"},
    {"type": "minimize_window", "title": "Notepad"},
    {"type": "maximize_window", "title": "Notepad"},
    {"type": "close_window", "title": "Notepad"},
    {"type": "system_control", "command": "volume_up", "steps": 3},
    {"type": "create_folder", "path": r"%USERPROFILE%\Desktop\New"},
    {"type": "write_file", "path": r"C:\Temp\note.txt", "content": ""},
    {"type": "copy_file", "source": r"C:\a.txt", "destination": r"C:\b.txt"},
    {"type": "move_file", "source": r"C:\a.txt", "destination": r"D:\a.txt"},
    {"type": "rename_file", "source": r"C:\a.txt", "destination": r"C:\c.txt"},
    {"type": "delete_file", "path": r"C:\Temp\old.txt"},
    {"type": "wait", "seconds": 1.5},
]


class PlanValidationTests(unittest.TestCase):
    def test_every_documented_action_type_passes(self):
        self.assertEqual({action["type"] for action in VALID_ACTIONS}, set(app.ACTION_FIELDS))
        for action in VALID_ACTIONS:
            with self.subTest(action=action):
                self.assertEqual(app.validate_action_plan(plan([dict(action)]), 960, 540, {1, 2, 3}), [action])

    def test_extra_fields_gemini_adds_are_dropped_instead_of_failing_the_plan(self):
        action = {"type": "click", "x": 1, "y": 2, "reason": "open the menu", "description": "File menu"}
        self.assertEqual(app.validate_action_plan(plan([action]), 960, 540), [{"type": "click", "x": 1, "y": 2}])

    def test_common_key_names_are_understood(self):
        for name in ("windows", "Escape", "arrowdown", "page_down", "spacebar"):
            with self.subTest(key=name):
                app.validate_action_plan(plan([{"type": "press_key", "key": name}]), 960, 540)
        self.assertEqual(app.VK_MAP[app.canonical_key("windows")], app.VK_MAP["win"])

    def test_malformed_actions_are_rejected_with_a_reason_gemini_can_fix(self):
        cases = [
            ({"type": "run_shell", "command": "anything"}, "unknown action type"),
            ("click here", "unknown action type"),
            ({"type": "type_text"}, "missing the field\\(s\\) text"),
            ({"type": "click_element", "id": 99}, "a control in the controls list"),
            ({"type": "point_at", "say": "here"}, "needs the id of a control, or x and y"),
            ({"type": "point_at", "id": 42}, "a control in the controls list"),
            ({"type": "click_element", "id": "2"}, "a control in the controls list"),
            ({"type": "click", "x": 960, "y": 10}, "inside the 960x540 screenshot"),
            ({"type": "click", "x": True, "y": 10}, "inside the 960x540 screenshot"),
            ({"type": "drag", "start_x": 1, "start_y": 2, "end_x": 3, "end_y": 900}, "end_y must be inside"),
            ({"type": "scroll", "amount": 1, "x": 5}, "needs both x and y"),
            ({"type": "scroll", "amount": 1.5}, "whole number"),
            ({"type": "press_key", "key": "hyperkey"}, "unknown key"),
            ({"type": "hotkey", "keys": []}, "1 to 5 keys"),
            ({"type": "open_path", "path": r"Documents\file.txt"}, "full path"),
            ({"type": "open_url", "url": "file:///C:/tools/x.exe"}, "http or https"),
            ({"type": "system_control", "command": "shutdown"}, "must be one of"),
            ({"type": "click", "x": 1, "y": 1, "clicks": 5}, "from 1 to 3"),
            ({"type": "wait", "seconds": 60}, "from 0 to 10"),
            ({"type": "type_text", "text": "x", "press_enter": "yes"}, "true or false"),
            ({"type": "focus_window", "title": "  "}, "text of at most"),
        ]
        for action, reason in cases:
            with self.subTest(action=action):
                with self.assertRaisesRegex(ValueError, reason):
                    app.validate_action_plan(plan([action]), 960, 540)

    def test_plan_level_problems_are_rejected_and_name_the_action(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            app.validate_action_plan(["not", "a", "plan"], 960, 540)
        with self.assertRaisesRegex(ValueError, "must be a list"):
            app.validate_action_plan({"actions": "press enter"}, 960, 540)
        with self.assertRaisesRegex(ValueError, "at most 10 actions"):
            app.validate_action_plan(plan([{"type": "wait", "seconds": 0}] * 11), 960, 540)
        with self.assertRaisesRegex(ValueError, "^Action 2: "):
            app.validate_action_plan(plan([VALID_ACTIONS[0], {"type": "nope"}]), 960, 540)
        self.assertEqual(app.validate_action_plan({"done": True, "message": "All set."}, 960, 540), [])


class RiskTests(unittest.TestCase):
    SHORTCUTS = {
        "windows powershell": r"C:\Start\Windows PowerShell.lnk",
        "spotify": r"C:\Start\Spotify.lnk",
    }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.existing = os.path.join(self.temp.name, "notes.txt")
        with open(self.existing, "w", encoding="utf-8") as handle:
            handle.write("keep me")
        for patcher in (
            mock.patch.object(app, "foreground_window_kind", return_value=None),
            mock.patch.object(app.Win32Input, "held_keys", set()),
            mock.patch.object(app, "discover_installed_shortcuts", return_value=self.SHORTCUTS),
            mock.patch.object(app, "discover_start_apps", return_value={}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_everyday_actions_run_without_asking(self):
        new_file = os.path.join(self.temp.name, "new.txt")
        safe = [
            {"type": "click", "x": 10, "y": 20},
            {"type": "scroll", "amount": -3},
            {"type": "type_text", "text": "hello"},
            {"type": "press_key", "key": "enter"},
            {"type": "hotkey", "keys": ["ctrl", "c"]},
            {"type": "hotkey", "keys": ["win", "d"]},
            {"type": "open_app", "app": "notepad"},
            {"type": "open_app", "app": "spotify"},
            {"type": "open_app", "app": "youtube"},
            {"type": "open_url", "url": "https://example.com"},
            {"type": "open_path", "path": self.temp.name},
            {"type": "open_path", "path": self.existing},
            {"type": "write_file", "path": new_file, "content": "hi"},
            {"type": "copy_file", "source": self.existing, "destination": new_file},
            {"type": "create_folder", "path": os.path.join(self.temp.name, "folder")},
            {"type": "system_control", "command": "volume_up"},
            {"type": "focus_window", "title": "Notepad"},
        ]
        for action in safe:
            with self.subTest(action=action):
                self.assertIsNone(app.risky_action_reason(action))

    def test_risky_actions_ask_first(self):
        os.makedirs(os.path.join(self.temp.name, "Archive"))
        with open(os.path.join(self.temp.name, "Archive", "notes.txt"), "w", encoding="utf-8"):
            pass
        cases = [
            ({"type": "delete_file", "path": self.existing}, "^Delete .*notes.txt\\?$"),
            ({"type": "delete_file", "path": self.temp.name}, "and everything in it"),
            ({"type": "write_file", "path": self.existing, "content": ""}, "^Overwrite "),
            ({"type": "write_file", "path": os.path.join(self.temp.name, "run.bat"), "content": "del *"}, "can run programs"),
            ({"type": "copy_file", "source": self.existing, "destination": self.existing + ".bak"}, None),
            ({"type": "move_file", "source": self.existing, "destination": os.path.join(self.temp.name, "Archive")}, "^Overwrite "),
            ({"type": "open_path", "path": r"C:\Downloads\setup.exe"}, "^Run "),
            ({"type": "open_app", "app": "powershell"}, "command terminal"),
            ({"type": "open_app", "app": "windows powershell"}, "command terminal"),
            ({"type": "open_app", "app": "terminal"}, "command terminal"),
            ({"type": "open_app", "app": "zzz unknown tool"}, "isn't one of your installed apps"),
            ({"type": "system_control", "command": "sleep_pc"}, "sleep"),
            ({"type": "system_control", "command": "lock"}, "Lock the PC"),
            ({"type": "close_window", "title": "Report.docx - Word"}, "Unsaved work"),
            ({"type": "hotkey", "keys": ["win", "r"]}, "Run box"),
            ({"type": "hotkey", "keys": "alt+f4"}, "Alt\\+F4"),
            ({"type": "hotkey", "keys": ["shift", "del"]}, "Permanently delete"),
            ({"type": "hotkey", "keys": ["winleft", "x"]}, "Win\\+X"),
            ({"type": "hotkey", "keys": ["l", "win"]}, "Win\\+L"),
        ]
        for action, question in cases:
            with self.subTest(action=action):
                if question is None:
                    self.assertIsNone(app.risky_action_reason(action))
                else:
                    self.assertRegex(app.risky_action_reason(action), question)

    def test_a_held_key_counts_toward_risky_shortcuts(self):
        with mock.patch.object(app.Win32Input, "held_keys", {"win"}):
            self.assertRegex(app.risky_action_reason({"type": "press_key", "key": "r"}), "Run box")
            self.assertRegex(app.risky_action_reason({"type": "key_down", "key": "x"}), "Win\\+X")

    def test_keystrokes_into_a_terminal_or_the_run_box_ask_first(self):
        with mock.patch.object(app, "foreground_window_kind", return_value="terminal"):
            self.assertRegex(app.risky_action_reason({"type": "type_text", "text": "dir"}), "type 'dir' in the terminal")
            self.assertRegex(app.risky_action_reason({"type": "press_key", "key": "enter"}), "press keys in the terminal")
            self.assertRegex(app.risky_action_reason({"type": "hotkey", "keys": ["ctrl", "v"]}), "in the terminal")
        with mock.patch.object(app, "foreground_window_kind", return_value="Run box"):
            self.assertRegex(app.risky_action_reason({"type": "type_text", "text": "cmd"}), "in the Run box")

    def test_start_search_asks_only_for_command_like_text(self):
        with mock.patch.object(app, "foreground_window_kind", return_value="Start search"):
            self.assertIsNone(app.risky_action_reason({"type": "type_text", "text": "spotify"}))
            self.assertIsNone(app.risky_action_reason({"type": "press_key", "key": "enter"}))
            self.assertRegex(app.risky_action_reason({"type": "type_text", "text": "powershell"}), "Start search")
            self.assertRegex(app.risky_action_reason({"type": "type_text", "text": r"notepad C:\x.txt"}), "Start search")


class ConfirmationTests(unittest.TestCase):
    def tearDown(self):
        app.publish_task_status(FakeBridge(), "Status: Ready!", running=False)

    def wait_for_question(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            pending = app.get_task_status()["confirm"]
            if pending:
                return pending
            time.sleep(0.01)
        self.fail("The question never reached the phone status.")

    def test_the_first_answer_unblocks_the_waiting_task(self):
        bridge = FakeBridge()
        result = {}
        question = r"Delete C:\Users\Test\old.txt?"
        worker = threading.Thread(target=lambda: result.update(answer=app.ask_user_to_confirm(bridge, question, timeout=5)))
        worker.start()
        pending = self.wait_for_question()
        self.assertEqual(pending["question"], question)
        self.assertFalse(app.answer_confirmation("stale-id", True))
        self.assertTrue(app.answer_confirmation(pending["id"], True))
        self.assertFalse(app.answer_confirmation(pending["id"], False))
        worker.join(5)
        self.assertIs(result["answer"], True)
        self.assertIsNone(app.get_task_status()["confirm"])
        self.assertEqual(bridge.confirm_sig.values, [(pending["id"], question)])
        self.assertEqual(bridge.confirm_done_sig.values, [(pending["id"],)])

    def test_dont_allow_is_reported_as_false(self):
        bridge = FakeBridge()
        result = {}
        worker = threading.Thread(target=lambda: result.update(answer=app.ask_user_to_confirm(bridge, "Lock the PC?", timeout=5)))
        worker.start()
        self.assertTrue(app.answer_confirmation(self.wait_for_question()["id"], False))
        worker.join(5)
        self.assertIs(result["answer"], False)

    def test_no_answer_in_time_counts_as_no(self):
        bridge = FakeBridge()
        self.assertIsNone(app.ask_user_to_confirm(bridge, "Lock the PC?", timeout=0.05))
        self.assertIsNone(app.get_task_status()["confirm"])
        request_id = bridge.confirm_sig.values[0][0]
        self.assertFalse(app.answer_confirmation(request_id, True))

    def test_phone_page_shows_the_question_and_sends_the_answer(self):
        page = app.ANDROID_WEB_CLIENT
        self.assertIn('id="confirmModal"', page)
        self.assertIn("showConfirm(state.confirm || null)", page)
        self.assertIn("requestUrl('/confirm', {id: id, allow: allow ? '1' : '0'})", page)
        self.assertIn("textContent = req.question", page)


VOICE = b"RIFF" + bytes(4000)


class ExecutorLoopTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("task_sleep", None), ("focus_for_paste", None)):
            # A task's waits, which don't go through time.sleep, and no look at this PC's real focus.
            patcher = mock.patch.object(app, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        app.PENDING_SEND.update(request="", time=0.0)
        self.addCleanup(app.PENDING_SEND.update, request="", time=0.0)
        app.LAST_RESEARCH.clear()
        self.addCleanup(app.LAST_RESEARCH.clear)

    def run_task(self, command, plans, confirm=(), screen=(1920, 1080, 960, 540), wav=b"",
                 controls=([], {}), execute=None, memory=(), research=None, same_screen=False,
                 quick=False, transcript=None, screen_changes=True):
        # quick: commands Motorcycle Man can do without Gemini are done that way (off, so these tests reach
        # Gemini); transcript: what writing down the recording gives (None: that fails);
        # screen_changes: whether the screen looks different after a step Gemini called done.
        sw, sh, iw, ih = screen
        bridge = FakeBridge()
        # Each screenshot differs, as on a real screen where actions change things, unless the test
        # wants a screen that never changes.
        shots = iter(range(1, 1000))

        def capture_screen():
            return ("same" if same_screen else f"screen-{next(shots)}"), sw, sh, iw, ih
        with tempfile.TemporaryDirectory() as temp_dir:
            os.makedirs(os.path.join(temp_dir, "Motorcycle Man"))
            with open(os.path.join(temp_dir, "Motorcycle Man", "keys.json"), "w", encoding="utf-8") as handle:
                json.dump({"gemini_api_key": "unit-test-key"}, handle)
            folders = {name: temp_dir for name in ("Desktop", "Downloads", "Documents")}
            with mock.patch.dict(os.environ, {"APPDATA": temp_dir}), \
                    mock.patch.object(app, "get_known_folder_paths", return_value=folders), \
                    mock.patch.object(app, "discover_installed_shortcuts", return_value={}), \
                    mock.patch.object(app, "discover_start_apps", return_value={}), \
                    mock.patch.object(app, "list_open_windows", return_value=["Notepad"]), \
                    mock.patch.object(app, "foreground_window_kind", return_value=None), \
                    mock.patch.object(app, "describe_window_controls", return_value=controls), \
                    mock.patch.object(app, "capture_screen_for_planner", side_effect=capture_screen) as capture, \
                    mock.patch.object(app, "call_gemini_planner", side_effect=plans) as gemini, \
                    mock.patch.object(app, "execute_single_action",
                                      side_effect=execute or (lambda act, *_, **__: "ran " + act["type"])) as run, \
                    mock.patch.object(app, "ask_user_to_confirm", side_effect=list(confirm)) as ask, \
                    mock.patch.object(app, "research", side_effect=research or AssertionError("no research expected")) as self.research, \
                    mock.patch.object(app, "wake_screen"), \
                    mock.patch.object(app, "window_to_work_on", return_value=None), \
                    mock.patch.object(app, "screen_fingerprint", return_value=("before", None)), \
                    mock.patch.object(app, "screen_changed", **({"side_effect": list(screen_changes)} if isinstance(
                        screen_changes, (list, tuple)) else {"return_value": screen_changes})), \
                    mock.patch.object(app, "transcribe_speech", **({"side_effect": RuntimeError("offline")}
                                                                   if transcript is None else {"return_value": transcript})), \
                    mock.patch.object(app, "quick_command", **({} if quick else {"return_value": None})), \
                    mock.patch.object(app.time, "sleep"):
                for command_before, result_before in memory:
                    app.remember(command_before, result_before)
                result = app.execute_smart_action(command, bridge, sw, sh, wav_bytes=wav)
                self.memory_after = app.load_memory()
                self.history_after = app.load_history()
        return result, bridge, gemini, run, ask, capture

    @staticmethod
    def media_types(parts):
        return [part["inline_data"]["mime_type"] for part in parts if "inline_data" in part]

    def test_a_spoken_command_is_kept_for_later_steps(self):
        plans = [
            dict(plan([{"type": "press_key", "key": "enter"}]), transcript="press enter in notepad"),
            plan([], "Pressed Enter.", done=True),
        ]
        result, _bridge, gemini, _run, _ask, _capture = self.run_task("", plans, wav=VOICE)
        self.assertEqual(result, "Pressed Enter.")
        first, second = (call.args[0] for call in gemini.call_args_list)
        self.assertEqual(self.media_types(first), ["image/jpeg", "audio/wav"])
        self.assertIn('"transcript" field', first[-1]["text"])
        self.assertEqual(self.media_types(second), ["image/jpeg"])
        self.assertIn("User command: press enter in notepad", second[-1]["text"])

    def test_the_recording_is_sent_again_until_gemini_says_what_it_heard(self):
        plans = [plan([{"type": "press_key", "key": "enter"}]), plan([], "Done.", done=True)]
        _result, _bridge, gemini, _run, _ask, _capture = self.run_task("", plans, wav=VOICE)
        self.assertEqual(self.media_types(gemini.call_args_list[1].args[0]), ["image/jpeg", "audio/wav"])

    def test_what_was_said_into_the_pc_is_written_down_and_handled_like_typed_words(self):
        # A spoken "scroll down" scrolls at once, with no planning; nothing said is "didn't catch that".
        with mock.patch.object(app.Win32Input, "scroll") as scroll:
            result, _bridge, gemini, *_ = self.run_task("", [], wav=VOICE, transcript="scroll down")
        self.assertEqual(result, "Scrolled down.")
        gemini.assert_not_called()
        self.assertEqual(scroll.call_count, 2)
        self.assertEqual(self.history_after[-1]["request"], "scroll down")
        result, _bridge, gemini, *_ = self.run_task("", [], wav=VOICE, transcript="")
        self.assertEqual(result, "I didn't catch that. Tap the helmet and speak again, or tap Type.")
        gemini.assert_not_called()

    def test_no_command_and_no_recording_does_nothing(self):
        result, _bridge, gemini, run, _ask, capture = self.run_task("", [], wav=bytes(100))
        self.assertEqual(result, "I didn't catch that. Tap the helmet and speak again, or tap Type.")
        gemini.assert_not_called()
        capture.assert_not_called()
        run.assert_not_called()

    def test_phone_page_reconnects_and_offers_the_android_app(self):
        page = app.ANDROID_WEB_CLIENT
        self.assertIn("if (res.status === 403) location.reload();", page)
        self.assertIn("Can't reach Motorcycle Man on your PC.", page)
        # Status checks run one at a time with a time limit, so a hung connection can't stall them.
        self.assertIn("if (checking) return;", page)
        self.assertIn("signal: stop.signal", page)
        self.assertIn("now - lastContact > 9000", page)  # a Wi-Fi hiccup is not "can't reach"
        self.assertIn("if (now - lastTick > 2000) lastContact", page)  # nor time spent asleep
        self.assertIn("#Intent;scheme=motorcycleman;package=app.motorcycleman.remote;S.browser_fallback_url=", page)
        self.assertIn("!/MotorcycleManApp/.test(navigator.userAgent)", page)

    def test_the_saved_gemini_key_moves_over_from_the_old_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            os.makedirs(os.path.join(temp_dir, "Googly"))
            with open(os.path.join(temp_dir, "Googly", "keys.json"), "w", encoding="utf-8") as handle:
                json.dump({"gemini_api_key": "saved-before-the-rename"}, handle)
            with mock.patch.dict(os.environ, {"APPDATA": temp_dir}):
                path = app.key_file_path()
            self.assertEqual(path, os.path.join(temp_dir, "Motorcycle Man", "keys.json"))
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["gemini_api_key"], "saved-before-the-rename")
            self.assertTrue(os.path.exists(os.path.join(temp_dir, "Googly", "keys.json")))

    def test_the_logo_is_bundled_and_the_phone_face_is_the_helmet(self):
        for name in ("motorcycle_man.ico", "motorcycle_man_icon.png"):
            self.assertTrue(os.path.isfile(app.resource_path(name)), name)
        page = app.ANDROID_WEB_CLIENT
        self.assertIn('<link rel="icon" href="/icon.png">', page)
        # The phone shows the same face as the PC: every mood's parts, shown by the face's data-mood,
        # and the visor in the mood's colours, each visor with gradients of its own.
        self.assertIn('<svg class="face-svg"', page)
        self.assertIn('class="face" id="face" data-mood="idle"', page)
        for mood in helmet_face.MOODS:
            visor = helmet_face.MOOD_VISOR[mood]
            self.assertIn(f'.face[data-mood="{mood}"] .m-{mood} {{ display: inline; }}', page)
            self.assertIn(f'.face[data-mood="{mood}"] .sheen-a {{ fill: url(#hm-sheen-{visor}); }}', page)
            self.assertIn(f'.face[data-mood="{mood}"] .glow {{ fill: url(#hm-glow-{visor}); }}', page)
            for paint in ("sheen", "low", "glow", "line"):
                self.assertIn(f'id="hm-{paint}-{visor}"', page)
        self.assertIn('const MOOD_LOOK = {"thinking": [0.7, -0.8]', page)
        self.assertIn("LOOK_REACH = [7, 5];", page)
        self.assertIn("if (busy) return pcMood;", page)
        for placeholder in ("/*FACE_CSS*/", "<!--FACE_SVG-->", "/*MOOD_LOOK*/", "/*LOOK_REACH*/"):
            self.assertNotIn(placeholder, page)
        self.assertNotIn("<!--FACE-->", app.JOIN_PAGE)

    def test_every_mood_draws_the_helmet(self):
        from PySide6.QtCore import QByteArray
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtSvg import QSvgRenderer
        QGuiApplication.instance() or QGuiApplication([])
        self.assertEqual(set(helmet_face.MOOD_PARTS), set(helmet_face.MOODS))
        self.assertEqual(set(helmet_face.MOOD_VISOR), set(helmet_face.MOODS))
        for mood in helmet_face.MOODS:
            eyes, brows, extras = helmet_face.MOOD_PARTS[mood]
            self.assertIn(helmet_face.MOOD_VISOR[mood], helmet_face.VISORS)
            for part in (eyes, brows, *extras):
                self.assertTrue(part is None or part in helmet_face.PARTS, part)
            for blink in (False, True):
                for with_extras in (True, False):
                    with self.subTest(mood=mood, blink=blink, extras=with_extras):
                        svg = helmet_face.face_svg(mood, (0.5, -0.5), blink, with_extras)
                        ET.fromstring(svg)  # well formed
                        self.assertTrue(QSvgRenderer(QByteArray(svg.encode())).isValid())
            # The extras drawn apart (for the rider) are exactly the mood's.
            self.assertEqual(bool(helmet_face.extras_svg(mood)), bool(extras))
        for svg in (helmet_face.phone_face_svg(), helmet_face.outline_svg(6), bike_rider.bike_svg(),
                    helmet_face.extras_svg("sleeping")):
            ET.fromstring(svg)

    def test_the_rider_and_its_helmet_paint(self):
        from PySide6.QtGui import QGuiApplication, QImage, QPainter
        QGuiApplication.instance() or QGuiApplication([])
        art = app.RiderArt(1.5)
        for pixmap in (art.body, art.edge):
            self.assertFalse(pixmap.isNull())
        body, edge = art.body.toImage(), art.edge.toImage()
        opaque = lambda image: sum(image.pixelColor(x, y).alpha() > 128 for y in range(0, image.height(), 3)
                                   for x in range(0, image.width(), 3))
        self.assertGreater(opaque(body), 500)
        self.assertGreater(opaque(edge), opaque(body))  # the light edge reaches round the bike and wheels
        image = QImage(120, 120, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        p = QPainter(image)
        app.paint_wheel(p, app.QPointF(30, 90), app.RIDER_WHEEL_R, 0.4, True)
        app.paint_helmet_face(p, 60, 50, 30, "happy", (0.2, 0.0), False, extras=False)
        app.paint_face_extras(p, 60, 50, 30, "sleeping")
        p.end()
        self.assertEqual(image.pixelColor(60, 50).alpha(), 255)  # the helmet
        self.assertEqual(image.pixelColor(30, 90).alpha(), 255)  # the wheel's hub
        first = app.svg_pixmap(("test", 1), lambda: helmet_face.face_svg("idle"), 40)
        self.assertIs(app.svg_pixmap(("test", 1), lambda: helmet_face.face_svg("oops"), 40), first)  # drawn once, then reused
        # The helmet sits on the rider's shoulders, its smoke comes from behind, and the wheels touch the ground.
        self.assertLess(app.RIDER_HEAD.y(), -40)
        self.assertLess(app.RIDER_PIPE.x(), 0)
        for wheel in app.RIDER_WHEELS:
            self.assertAlmostEqual(wheel.y() + app.RIDER_WHEEL_R, 0, delta=0.01)

    def test_every_script_on_the_pages_is_valid_javascript(self):
        # One mistake (say, a name used twice) stops a whole script, and the phone page with it. Node,
        # where it is installed, reads each script without running it.
        import re
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("Node isn't installed")
        scripts = [("phone page", script) for script in re.findall(r"<script>(.*?)</script>", app.ANDROID_WEB_CLIENT, re.S)]
        scripts += [("join page", script) for script in re.findall(r"<script>(.*?)</script>", app.JOIN_PAGE, re.S)]
        scripts += [("service worker", app.SERVICE_WORKER)] if hasattr(app, "SERVICE_WORKER") else []
        self.assertGreaterEqual(len(scripts), 2)
        with tempfile.TemporaryDirectory() as temp_dir:
            for n, (where, script) in enumerate(scripts):
                path = os.path.join(temp_dir, f"script{n}.js")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(script)
                checked = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=30)
                self.assertEqual(checked.returncode, 0, f"{where}: {checked.stderr}")

    def test_phone_page_listens_with_the_android_apps_speech_recognition(self):
        page = app.ANDROID_WEB_CLIENT
        self.assertIn("window.MotorcycleManAndroid && window.MotorcycleManAndroid.isAvailable() ? window.MotorcycleManAndroid : null", page)
        self.assertIn("androidSpeech.startListening(recognitionLang);", page)
        self.assertIn("androidSpeech.stopListening();", page)
        self.assertIn("Nothing was heard on the phone; don't fall back to the PC's microphone.", page)
        for callback in ("window.onAndroidSpeech =", "window.onAndroidSpeechError =", "window.onAndroidNotice ="):
            self.assertIn(callback, page)

    def test_a_task_that_wont_stop_keeps_a_new_command_from_running_for_a_while(self):
        app.TASK_LOCK.acquire()  # a task stuck where it can't stop
        try:
            with mock.patch.object(app, "_execute_smart_action_locked") as execute, \
                    mock.patch.object(app, "STOP_WAIT_SECONDS", 0.2):
                result = app.execute_smart_action("Open File Explorer", FakeBridge(), 1000, 800)
        finally:
            app.TASK_LOCK.release()
        self.assertEqual(result, "I'm still stopping the last task. Try again in a moment.")
        execute.assert_not_called()

    def test_task_status_is_shared_with_the_phone_and_desktop(self):
        bridge = FakeBridge()
        app.publish_task_status(bridge, "Status: Step 2 — analyzing screen with Gemini...", running=True)
        try:
            status = app.get_task_status()
            self.assertEqual((status["running"], status["status"], status["confirm"]),
                             (True, "Status: Step 2 — analyzing screen with Gemini...", None))
            self.assertEqual(bridge.status_sig.values[-1], ("Status: Step 2 — analyzing screen with Gemini...",))
            self.assertIn("fetch(requestUrl('/status', phoneBattery)", app.ANDROID_WEB_CLIENT)
        finally:
            app.publish_task_status(bridge, "Status: Ready!", running=False)

    def test_phone_requests_require_the_per_run_session_token(self):
        token = "disposable-session-token"
        self.assertTrue(app.phone_request_is_authorized("text=open+notepad&token=" + token, token))
        self.assertFalse(app.phone_request_is_authorized("text=open+notepad", token))
        self.assertFalse(app.phone_request_is_authorized("token=wrong-token", token))
        self.assertFalse(app.phone_request_is_authorized("token=", ""))
        self.assertIn("new URLSearchParams(window.location.search)", app.ANDROID_WEB_CLIENT)
        self.assertIn("fetch(requestUrl('/cmd'", app.ANDROID_WEB_CLIENT)

    def test_invalid_plan_gets_one_corrective_replan_before_any_action(self):
        plans = [
            plan([{"type": "click", "x": 999, "y": 10}]),
            plan([{"type": "press_key", "key": "enter"}]),
            plan([], "Done.", done=True),
        ]
        result, _bridge, gemini, run, _ask, _capture = self.run_task("Press Enter", plans, screen=(1000, 800, 500, 400))
        self.assertEqual(result, "Done.")
        self.assertEqual(gemini.call_count, 3)
        first_text = gemini.call_args_list[0].args[0][-1]["text"]
        retry_text = gemini.call_args_list[1].args[0][-1]["text"]
        self.assertNotIn("previous plan was rejected", first_text)
        self.assertIn("previous plan was rejected: Action 1: click.x must be inside the 500x400 screenshot", retry_text)
        self.assertEqual([call.args[0]["type"] for call in run.call_args_list], ["press_key"])

    def test_a_control_number_not_on_the_list_gets_told_to_click_by_place(self):
        plans = [
            plan([{"type": "click_element", "id": 999}]),
            plan([{"type": "click", "x": 100, "y": 100}]),
            plan([], "Done.", done=True),
        ]
        result, _bridge, gemini, run, _ask, _capture = self.run_task("Click the blue button", plans, screen=(1000, 800, 500, 400))
        self.assertEqual(result, "Done.")
        retry_text = gemini.call_args_list[1].args[0][-1]["text"]
        self.assertIn("click_element.id must be the number of a control", retry_text)
        self.assertIn("use a click with x and y read from the screenshot", retry_text)
        self.assertEqual([call.args[0]["type"] for call in run.call_args_list], ["click"])

    def test_rejected_plans_run_none_of_their_actions_and_stop_after_three_tries(self):
        bad = plan([{"type": "press_key", "key": "enter"}, {"type": "run_shell", "command": "must never run"}])
        result, _bridge, gemini, run, _ask, _capture = self.run_task("Press Enter safely", [bad] * 6)
        self.assertIn("Stopped after 3 failed tries in a row", result)
        self.assertIn("unknown action type 'run_shell'", result)
        self.assertEqual(gemini.call_count, 6)
        run.assert_not_called()

    def test_a_failed_action_is_reported_to_gemini_and_the_task_continues(self):
        def execute(act, *_, **__):
            if act["type"] == "focus_window":
                raise RuntimeError("no window called Outlook")
            return "ran " + act["type"]
        plans = [plan([{"type": "focus_window", "title": "Outlook"}, {"type": "press_key", "key": "enter"}]),
                 plan([{"type": "open_app", "app": "outlook"}]),
                 plan([], "Opened Outlook instead.", done=True)]
        result, _bridge, gemini, run, _ask, _capture = self.run_task("Show my mail", plans, execute=execute, confirm=[True])
        self.assertEqual(result, "Opened Outlook instead.")
        self.assertIn("FAILED: focus_window - no window called Outlook", gemini.call_args_list[1].args[0][-1]["text"])
        self.assertEqual([call.args[0]["type"] for call in run.call_args_list], ["focus_window", "open_app"])

    def test_three_failed_steps_in_a_row_stop_the_task(self):
        def execute(act, *_, **__):
            raise OSError("Windows accepted 0 of 2 key events")
        result, *_ = self.run_task("Type hello", [plan([{"type": "type_text", "text": "hello"}])] * 3, execute=execute)
        self.assertEqual(result, "Stopped after 3 failed tries in a row. "
                                 "Last problem: FAILED: type_text - Windows accepted 0 of 2 key events")

    def test_gemini_sees_the_window_controls_and_click_element_uses_them(self):
        controls = (["[1] row 'Alice, Lunch on Friday?' at (480, 210)", "[2] row 'Bob, Invoice' at (480, 250)"],
                    {1: (960, 420), 2: (960, 500)})
        plans = [plan([{"type": "click_element", "id": 1}]), plan([], "Opened the top email.", done=True)]
        result, _bridge, gemini, run, _ask, _capture = self.run_task("open the top email", plans, controls=controls)
        self.assertEqual(result, "Opened the top email.")
        prompt = gemini.call_args_list[0].args[0][-1]["text"]
        self.assertIn("[1] row 'Alice, Lunch on Friday?' at (480, 210)", prompt)
        self.assertEqual(run.call_args.kwargs["controls"], {1: (960, 420), 2: (960, 500)})
        # The next step is told which control was clicked, not just its number.
        self.assertIn("Clicked [1] row 'Alice, Lunch on Friday?'", gemini.call_args_list[1].args[0][-1]["text"])

    EMAIL_CONTROLS = (["[1] row 'Alice, Lunch on Friday?' at (480, 210)", "[2] button 'Send \u202a(Ctrl-Enter)\u202c' at (100, 300)",
                       "[3] button 'Post' at (700, 120)"], {1: (960, 420), 2: (200, 600), 3: (1400, 240)})

    def test_the_buddy_glides_to_each_target_and_narrates(self):
        plans = [plan([{"type": "click_element", "id": 1, "say": "Opening Alice's email"},
                       {"type": "type_text", "text": "hi"}]),
                 plan([], "Opened it.", done=True)]
        with mock.patch.object(app, "BUDDY_HIDDEN_FROM_SCREENSHOTS", True):
            result, bridge, *_ = self.run_task("open the top email and type hi", plans, controls=self.EMAIL_CONTROLS)
        self.assertEqual(result, "Opened it.")
        # It runs to the click with the mouse (True), then plays a ripple there and the typed keys,
        # and confetti once the task is done.
        self.assertEqual(bridge.pointer_sig.values, [(960, 420, "Opening Alice's email", True)])
        self.assertEqual(bridge.say_sig.values, [("Looking at your screen...", 0), ("Typing", 0), ("Opened it.", 9000)])
        self.assertEqual(bridge.act_sig.values, [("click", {"x": 960, "y": 420, "clicks": 1, "button": "left"}),
                                                 ("type", {"text": "hi"}), ("done", {})])
        self.assertEqual(bridge.hide_sig.values, [])

    def test_where_the_buddy_cant_be_hidden_from_screenshots_it_stays_away_while_working(self):
        plans = [plan([{"type": "click_element", "id": 1}]), plan([], "Opened it.", done=True)]
        with mock.patch.object(app, "BUDDY_HIDDEN_FROM_SCREENSHOTS", False):
            _result, bridge, *_ = self.run_task("open the top email", plans, controls=self.EMAIL_CONTROLS)
        self.assertEqual(len(bridge.hide_sig.values), 1)
        self.assertEqual(bridge.pointer_sig.values, [])
        self.assertEqual(bridge.say_sig.values, [("Opened it.", 9000)])

    def test_point_at_moves_the_buddy_without_clicking(self):
        plans = [plan([{"type": "point_at", "id": 3, "say": "Post is right here"}], "That's the Post button.")]
        with mock.patch.object(app, "BUDDY_HIDDEN_FROM_SCREENSHOTS", True):
            result, bridge, _gemini, run, *_ = self.run_task("where is the post button", plans, controls=self.EMAIL_CONTROLS)
        self.assertEqual(bridge.pointer_sig.values, [(1400, 240, "Post is right here", False)])  # the mouse stays put
        self.assertEqual([call.args[0]["type"] for call in run.call_args_list], ["point_at"])
        self.assertEqual(result, "That's the Post button.")

    def test_gemini_holds_the_final_send_until_the_user_says_yes(self):
        typed = plan([{"type": "type_text", "text": "Building something cool."}], "Tweet ready, say yes to post it")
        typed["awaiting_yes"] = True
        result, _bridge, _gemini, run, *_ = self.run_task("tweet building something cool", [typed])
        self.assertEqual(result, "Tweet ready, say yes to post it")
        self.assertEqual(app.PENDING_SEND["request"], "tweet building something cool")
        posted = [plan([{"type": "click_element", "id": 3}])]  # the Post button; then Motorcycle Man reports without looking again
        result, _bridge, gemini, run, *_ = self.run_task("nice job, yes", posted, controls=self.EMAIL_CONTROLS)
        self.assertEqual(result, "Posted!")
        self.assertEqual(gemini.call_count, 1)
        prompt = gemini.call_args_list[0].args[0][-1]["text"]
        self.assertIn('Finish their earlier request now, including the final send/post/run step you held back: '
                      '"tweet building something cool"', prompt)
        self.assertIn("The user has said yes: do the final send/post/run step now.", prompt)
        self.assertEqual(run.call_args_list[0].args[0]["type"], "click_element")
        self.assertEqual(app.PENDING_SEND["request"], "")

    def test_a_send_button_gemini_tries_to_click_is_held_for_a_yes(self):
        plans = [plan([{"type": "type_text", "text": "Thanks, see you Friday"}, {"type": "click_element", "id": 2}])]
        result, _bridge, _gemini, run, *_ = self.run_task("reply thanks see you friday", plans, controls=self.EMAIL_CONTROLS)
        self.assertEqual(result, "Ready. Say yes to send it.")
        self.assertEqual([call.args[0]["type"] for call in run.call_args_list], ["type_text"])
        self.assertEqual(app.PENDING_SEND["request"], "reply thanks see you friday")

    def test_no_cancels_a_held_send_and_another_command_drops_it(self):
        app.PENDING_SEND.update(request="tweet hello", time=time.time())
        result, _bridge, gemini, *_ = self.run_task("no, don't", [])
        self.assertEqual(result, "Okay, I won't send it.")
        gemini.assert_not_called()
        app.PENDING_SEND.update(request="tweet hello", time=time.time())
        _result, _bridge, gemini, *_ = self.run_task("open the top email", [plan([], "Done.", done=True)])
        self.assertNotIn("Finish their earlier request", gemini.call_args.args[0][-1]["text"])
        self.assertEqual(app.PENDING_SEND["request"], "")

    def test_yes_and_no_are_recognized(self):
        for text in ("yes", "Yes!", "yeah do it", "nice job, yes", "go ahead", "send it", "yep post it", "sure"):
            with self.subTest(text=text):
                self.assertTrue(app.is_affirmation(text))
                self.assertFalse(app.is_refusal(text))
        for text in ("no", "No, don't send it", "cancel", "wait"):
            with self.subTest(text=text):
                self.assertTrue(app.is_refusal(text))
                self.assertFalse(app.is_affirmation(text))
        for text in ("open the top email", "yes open chrome and search for the weather in paris today"):
            with self.subTest(text=text):
                self.assertFalse(app.is_affirmation(text))
        self.assertEqual(app.final_send_verb({"type": "hotkey", "keys": ["ctrl", "enter"]}, []), "send")
        self.assertIsNone(app.final_send_verb({"type": "hotkey", "keys": ["ctrl", "c"]}, []))

    def test_only_send_buttons_are_held_not_boxes_or_tabs_that_mention_posting(self):
        lines = ["[1] edit 'Post text' at (1, 1)", "[2] button 'Post' at (1, 1)", "[3] tab 'Posts' at (1, 1)",
                 "[4] button 'Send \u202a(Ctrl-Enter)\u202c' at (1, 1)", "[5] button 'Posting tips' at (1, 1)",
                 "[6] link 'Post' at (1, 1)", "[7] button 'Run' at (1, 1)", "[8] button 'Send now' at (1, 1)"]
        verbs = {n: app.final_send_verb({"type": "click_element", "id": n}, lines) for n in range(1, 9)}
        self.assertEqual(verbs, {1: None, 2: "post", 3: None, 4: "send", 5: None, 6: None, 7: "run", 8: "send"})

    def test_typing_happens_before_the_send_button_is_held(self):
        controls = (["[1] edit 'Post text' at (400, 100)", "[2] button 'Post' at (550, 120)"], {1: (800, 200), 2: (1100, 240)})
        plans = [plan([{"type": "click_element", "id": 1}, {"type": "type_text", "text": "Building something cool."},
                       {"type": "click_element", "id": 2}])]
        result, _bridge, _gemini, run, *_ = self.run_task("tweet building something cool", plans, controls=controls)
        self.assertEqual(result, "Ready. Say yes to post it.")
        self.assertEqual([call.args[0]["type"] for call in run.call_args_list], ["click_element", "type_text"])

    def test_pointing_finishes_without_another_gemini_call(self):
        controls = (["[1] button 'Post' at (550, 120)"], {1: (1100, 240)})
        plans = [plan([{"type": "point_at", "id": 1, "say": "Post is here"}], "That's the Post button.")]
        result, _bridge, gemini, *_ = self.run_task("where is the post button", plans, controls=controls)
        self.assertEqual(result, "That's the Post button.")
        self.assertEqual(gemini.call_count, 1)

    def test_click_element_clicks_the_middle_of_the_listed_control(self):
        with mock.patch.object(app.Win32Input, "click") as click:
            app.execute_single_action({"type": "click_element", "id": 2, "clicks": 2}, 1920, 1080, 960, 540,
                                      FakeBridge(), controls={2: (960, 500)})
        click.assert_called_once_with(960, 500, button="left", clicks=2)

    def test_recent_requests_are_remembered_for_follow_up_commands(self):
        plans = [plan([], "Opened the top email.", done=True)]
        _result, _bridge, gemini, *_ = self.run_task("open the top one", plans, memory=[("open gmail", "Opened gmail.")])
        prompt = gemini.call_args_list[0].args[0][-1]["text"]
        self.assertIn('the user said "open gmail"; result: Opened gmail.', prompt)
        self.assertEqual([entry["command"] for entry in self.memory_after], ["open gmail", "open the top one"])

    def test_memory_keeps_only_recent_requests(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, {"APPDATA": temp_dir}):
            for n in range(app.MEMORY_LIMIT + 3):
                app.remember(f"command {n}", "done")
            entries = app.load_memory()
            self.assertEqual(len(entries), app.MEMORY_LIMIT)
            self.assertEqual(entries[-1]["command"], f"command {app.MEMORY_LIMIT + 2}")
            with mock.patch.object(app.time, "time", return_value=time.time() + app.MEMORY_MAX_AGE_SECONDS + 60):
                self.assertEqual(app.load_memory(), [])

    def test_newer_gemini_keys_with_a_dot_are_kept_intact(self):
        self.assertEqual(app.clean_api_key("  AQ.Ab8RN6Lx-y_z12 \n"), "AQ.Ab8RN6Lx-y_z12")
        self.assertEqual(app.clean_api_key("AIzaSyD-abc_123"), "AIzaSyD-abc_123")
        _result, _bridge, gemini, *_ = self.run_task("Do a harmless test", [plan([], "Done.", done=True)])
        self.assertEqual(gemini.call_args.args[1], "unit-test-key")

    def test_the_gemini_key_goes_in_a_header_and_busy_answers_are_retried(self):
        busy = HTTPError("https://example.invalid", 503, "busy", {}, io.BytesIO(b'{"error": {"message": "High demand"}}'))
        answer = mock.MagicMock()
        answer.__enter__.return_value.read.return_value = json.dumps(
            {"candidates": [{"content": {"parts": [{"text": '{"actions": []}'}]}}]}).encode()
        with mock.patch.object(app.urllib.request, "urlopen", side_effect=[busy, answer]) as urlopen, \
                mock.patch.object(app.time, "sleep"):
            self.assertEqual(app.call_gemini_planner([{"text": "hi"}], "secret-key"), {"actions": []})
        first, second = (call.args[0] for call in urlopen.call_args_list)
        self.assertEqual(first.full_url, second.full_url)  # retried the same model
        self.assertNotIn("secret-key", first.full_url)
        self.assertEqual(first.get_header("X-goog-api-key"), "secret-key")

    def test_a_model_over_its_free_limit_sits_out_for_a_minute(self):
        app.RESTING_MODELS.clear()
        self.addCleanup(app.RESTING_MODELS.clear)
        first, second = app.GEMINI_MODELS[:2]

        def models_asked(*answers):
            # Which models one planning request asks, in order, and how often it waits to retry.
            with mock.patch.object(app.urllib.request, "urlopen", side_effect=answers) as urlopen, \
                    mock.patch.object(app.time, "sleep") as sleep:
                self.assertEqual(app.call_gemini_planner([{"text": "hi"}], "key"), {"actions": []})
            return [call.args[0].full_url.split("/models/")[1].split(":")[0] for call in urlopen.call_args_list], sleep.call_count

        def over_limit():
            return HTTPError("https://example.invalid", 429, "quota", {},
                             io.BytesIO(b'{"error": {"message": "You exceeded your current quota", "status": "RESOURCE_EXHAUSTED"}}'))

        def plan_answer():
            return http_answer(json.dumps({"candidates": [{"content": {"parts": [{"text": '{"actions": []}'}]}}]}))

        # Quota exceeded: no waiting and no retry; the next model answers ...
        self.assertEqual(models_asked(over_limit(), plan_answer()), ([first, second], 0))
        self.assertAlmostEqual(app.RESTING_MODELS[first] - time.monotonic(), app.GEMINI_REST_SECONDS, delta=5)
        # ... and for the next minute that model isn't asked at all, by planning or research.
        self.assertEqual(models_asked(plan_answer()), ([second], 0))
        self.assertEqual(app.models_to_try(app.RESEARCH_MODELS), [m for m in app.RESEARCH_MODELS if m != first])
        app.RESTING_MODELS[first] = time.monotonic() - 1  # the minute is up
        self.assertEqual(models_asked(plan_answer()), ([first], 0))
        # When every model is resting, they're all asked anyway rather than giving up.
        for model in app.GEMINI_MODELS:
            app.RESTING_MODELS[model] = time.monotonic() + 30
        self.assertEqual(models_asked(*[over_limit() for _ in app.GEMINI_MODELS[1:]], plan_answer()),
                         (app.GEMINI_MODELS, 0))

    def test_a_daily_limit_rests_the_model_until_google_says_to_come_back(self):
        app.RESTING_MODELS.clear()
        self.addCleanup(app.RESTING_MODELS.clear)
        daily = {"message": "You exceeded your current quota", "status": "RESOURCE_EXHAUSTED", "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": []},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "71747s"}]}
        self.assertEqual(app.rest_seconds(daily), 71747)
        for delay, rest in (("5s", 60), ("2.5s", 60), ("999999s", 24 * 3600), ("soon", 60)):
            with self.subTest(delay=delay):
                self.assertEqual(app.rest_seconds({"details": [{"@type": "google.rpc.RetryInfo", "retryDelay": delay}]}), rest)
        self.assertEqual((app.rest_seconds({}), app.rest_seconds("not an error")), (60, 60))
        over_limit = HTTPError("https://example.invalid", 429, "quota", {}, io.BytesIO(json.dumps({"error": daily}).encode()))
        answer = http_answer(json.dumps({"candidates": [{"content": {"parts": [{"text": '{"actions": []}'}]}}]}))
        with mock.patch.object(app.urllib.request, "urlopen", side_effect=[over_limit, answer]):
            app.call_gemini_planner([{"text": "hi"}], "key")
        self.assertAlmostEqual(app.RESTING_MODELS[app.GEMINI_MODELS[0]] - time.monotonic(), 71747, delta=5)

    def test_done_plans_with_estimated_clicks_are_checked_on_a_fresh_screenshot(self):
        plans = [
            plan([{"type": "click", "x": 100, "y": 200}], "Saved the note.", done=True),
            plan([], "Verified: the note is saved.", done=True),
        ]
        result, _bridge, gemini, run, _ask, capture = self.run_task("Save the note", plans)
        self.assertEqual(result, "Verified: the note is saved.")
        self.assertEqual(gemini.call_count, 2)
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(run.call_count, 1)

    def test_done_plans_with_exact_actions_finish_without_another_look(self):
        controls = (["[1] button 'Save' at (100, 200)"], {1: (200, 400)})
        plans = [plan([{"type": "click_element", "id": 1}, {"type": "hotkey", "keys": ["ctrl", "s"]}], "Saved the note.", done=True)]
        result, _bridge, gemini, run, _ask, capture = self.run_task("Save the note", plans, controls=controls)
        self.assertEqual(result, "Saved the note.")
        self.assertEqual((gemini.call_count, capture.call_count, run.call_count), (1, 1, 2))

    def test_a_done_step_that_changed_nothing_on_the_screen_gets_another_look(self):
        # Clicking the middle of an email row once only selected it: Gemini said done, but the screen
        # was the same, so Motorcycle Man looks again rather than say it opened the email.
        controls = (["[1] row 'Alice, Lunch' at (100, 200)"], {1: (200, 400)})
        plans = [plan([{"type": "click_element", "id": 1}], "Opened the top email.", done=True),
                 plan([{"type": "click_element", "id": 1}], "Opened it now.", done=True)]
        result, _bridge, gemini, run, *_ = self.run_task("open the top email", plans, controls=controls,
                                                         screen_changes=[False, True])
        self.assertEqual((result, gemini.call_count, run.call_count), ("Opened it now.", 2, 2))
        result, _bridge, gemini, *_ = self.run_task("open the top email", plans[:1], controls=controls)
        self.assertEqual((result, gemini.call_count), ("Opened the top email.", 1))
        self.assertIn('Also set "done": true together with actions when those actions finish the request',
                      app.PLANNER_SYSTEM_INSTRUCTIONS)

    def test_a_done_step_done_twice_more_is_taken_as_done(self):
        # In WhatsApp the first click opened the chat, and Gemini then clicked the open chat again
        # and again, saying done each time. A second repeat is not run: the chat is open.
        click = lambda x, y: {"type": "click", "x": x, "y": y}
        plans = [plan([click(120, 260)], "Opened the second chat.", done=True),
                 plan([click(121, 262)], "Opened the second chat.", done=True),
                 plan([click(119, 259)], "Opened the second chat.", done=True)]
        result, _bridge, gemini, run, *_ = self.run_task("open the second chat", plans)
        self.assertEqual((result, gemini.call_count, run.call_count), ("Opened the second chat.", 3, 2))
        # Each look after a step that said done tells Gemini so.
        self.assertIn("Your last step said it finished the request", gemini.call_args_list[1].args[0][-1]["text"])
        self.assertNotIn("Your last step said it finished the request", gemini.call_args_list[0].args[0][-1]["text"])

    def test_gemini_keeps_a_note_of_what_is_left(self):
        plans = [dict(plan([{"type": "focus_window", "title": "Google Gemini"}]), todo="copy the poem, paste it in Notes"),
                 plan([], "Done.", done=True)]
        _result, _bridge, gemini, *_ = self.run_task("copy the poem from Gemini into my notes", plans)
        self.assertIn("Still to do, from your last step: copy the poem, paste it in Notes",
                      gemini.call_args_list[1].args[0][-1]["text"])
        for rule in ('"todo"', "switch to it with focus_window", 'check "Copied during this task"'):
            self.assertIn(rule, app.PLANNER_SYSTEM_INSTRUCTIONS)

    def test_gemini_sees_what_was_copied_during_the_task_and_nothing_else(self):
        poem = "Silver waves roll in at dawn,\nWhispering secrets, then they're gone."
        sequence = iter([7, 8, 8, 8, 8])  # at the start, then after the click: it copied something
        plans = [plan([{"type": "click", "x": 450, "y": 280}]), plan([], "Done.", done=True)]
        with mock.patch.object(app, "clipboard_sequence", side_effect=lambda: next(sequence)), \
                mock.patch.object(app, "clipboard_text", return_value=poem) as read:
            _result, _bridge, gemini, *_ = self.run_task("copy the poem", plans)
        second = gemini.call_args_list[1].args[0][-1]["text"]
        self.assertIn(f"Copied during this task (on the clipboard now, {len(poem)} characters): " + json.dumps(poem), second)
        self.assertIn(f'(copied {len(poem)} characters: \\"Silver waves roll in at dawn, Whispering', second)
        self.assertEqual(read.call_count, 1)
        # What was on the clipboard before the task is never read, let alone sent.
        plans = [plan([{"type": "click", "x": 450, "y": 280}]), plan([], "Done.", done=True)]
        with mock.patch.object(app, "clipboard_sequence", return_value=7), \
                mock.patch.object(app, "clipboard_text", return_value="my password") as read:
            _result, _bridge, gemini, *_ = self.run_task("click it", plans)
        read.assert_not_called()
        self.assertNotIn("Copied during this task (", gemini.call_args_list[1].args[0][-1]["text"])

    def test_a_paste_is_checked(self):
        # In testing, Gemini pressed ctrl+v without clicking into the note, pasted nothing, and said it had.
        paste = {"type": "hotkey", "keys": ["ctrl", "v"]}
        for focus, note in (([app.NOT_A_TEXT_BOX, app.NOT_A_TEXT_BOX], "the keyboard focus wasn't in a text box"),
                            ([0, 68], "pasted: the text box went from 0 to 68 characters"),
                            ([12, 12], "the text box didn't change")):
            with self.subTest(note=note):
                plans = [plan([paste]), plan([], "Pasted it.", done=True)]
                with mock.patch.object(app, "focus_for_paste", side_effect=focus):
                    _result, _bridge, gemini, *_ = self.run_task("paste it", plans)
                self.assertIn(note, gemini.call_args_list[1].args[0][-1]["text"])
        self.assertEqual([app.is_paste({"type": "hotkey", "keys": keys}) for keys in
                          (["Ctrl", "V"], ["shift", "insert"], ["ctrl", "c"], ["ctrl", "shift", "v"])],
                         [True, True, False, False])
        self.assertEqual(app.paste_note(None, 5), "")  # can't tell: nothing is said

    def test_simple_scrolls_happen_at_once_without_gemini(self):
        cases = {
            "scroll down": ("down", 6, False), "Scroll up a bit.": ("up", 3, False),
            "please scroll down slowly": ("down", 12, True), "scroll down a lot": ("down", 12, False),
            "scroll to the top": ("up", app.SCROLL_TO_END, False), "scroll all the way down": ("down", app.SCROLL_TO_END, False),
            "page down": ("down", 10, False), "go to the bottom of the page": ("down", app.SCROLL_TO_END, False),
            "scroll right": ("right", 6, False),
        }
        for command, wanted in cases.items():
            with self.subTest(command=command):
                self.assertEqual(app.scroll_request(command), wanted)
        for command in ("scroll down until you see the email from Amazon", "scroll down in chrome", "go back",
                        "open the top email", "scrolling is slow"):
            with self.subTest(command=command):
                self.assertIsNone(app.scroll_request(command))
        with mock.patch.object(app.Win32Input, "scroll") as scroll:
            result, bridge, gemini, run, _ask, capture = self.run_task("Scroll down slowly", [])
        self.assertEqual(result, "Scrolled down slowly.")
        gemini.assert_not_called()
        capture.assert_not_called()
        self.assertEqual([call.args[0] for call in scroll.call_args_list], [-1] * 12)  # one notch at a time, downward
        self.assertEqual(bridge.mood_sig.values[0], ("scrolling",))
        self.assertEqual(bridge.pointer_sig.values[0][2:], ("Scrolling down...", True))  # the buddy takes the mouse there
        self.assertEqual(bridge.act_sig.values[0], ("scroll", {"direction": "down", "ms": 2040}))  # 12 notches, 0.17 s apart

    def test_gemini_scrolls_go_the_way_it_says(self):
        with mock.patch.object(app.Win32Input, "scroll") as scroll:
            for action, wheel in (({"type": "scroll", "direction": "down", "amount": 5}, (-5, False)),
                                  ({"type": "scroll", "direction": "up"}, (5, False)),
                                  ({"type": "scroll", "direction": "left", "amount": -3}, (-3, True)),
                                  ({"type": "scroll", "amount": -4}, (-4, False))):  # the older signed form
                app.validate_action(action, 960, 540)
                app.execute_single_action(action, 1920, 1080, 960, 540, FakeBridge())
                self.assertEqual((scroll.call_args.args[0], scroll.call_args.kwargs["horizontal"]), wheel)
        with self.assertRaises(ValueError):
            app.validate_action({"type": "scroll"}, 960, 540)
        with self.assertRaises(ValueError):
            app.validate_action({"type": "scroll", "direction": "sideways"}, 960, 540)

    def test_actions_that_change_nothing_on_the_screen_stop_soon(self):
        scroll = {"type": "scroll", "direction": "down", "amount": 5, "x": 500, "y": 300}
        plans = [plan([dict(scroll)]) for _ in range(6)]
        result, _bridge, gemini, run, *_ = self.run_task("find the newsletter from last week", plans, same_screen=True)
        self.assertEqual(result, "Stopped: the screen didn't change after 3 tries. "
                                 "Done so far: ran scroll, ran scroll, ran scroll.")
        self.assertEqual(gemini.call_count, 3)
        self.assertIn("they changed nothing", gemini.call_args.args[0][-1]["text"])

    def test_after_a_yes_the_send_happens_once(self):
        controls = (["[1] edit 'Prompt' at (400, 500)", "[2] button 'Send' at (600, 500)"], {1: (800, 1000), 2: (1200, 1000)})
        for send in ({"type": "click_element", "id": 2}, {"type": "press_key", "key": "enter"},
                     {"type": "hotkey", "keys": ["ctrl", "enter"]}, {"type": "type_text", "text": "", "press_enter": True}):
            with self.subTest(send=send["type"]):
                app.PENDING_SEND.update(request="type thanks to Claude", time=time.time())
                # Gemini plans the send plus more, and would plan another send if asked to look again.
                plans = [plan([{"type": "click_element", "id": 1}, dict(send), {"type": "press_key", "key": "enter"}],
                              "Sent your message!", done=False),
                         plan([{"type": "click_element", "id": 2}], "Sending again", done=True)]
                result, _bridge, gemini, run, *_ = self.run_task("send it", plans, controls=controls)
                self.assertEqual(result, "Sent your message!")
                self.assertEqual(gemini.call_count, 1)
                self.assertEqual([call.args[0]["type"] for call in run.call_args_list], ["click_element", send["type"]])

    def test_a_try_where_nothing_was_heard_keeps_a_held_send_waiting(self):
        app.PENDING_SEND.update(request="reply with hello", time=time.time())
        result, *_ = self.run_task("", [], wav=bytes(100))
        self.assertEqual(result, "I didn't catch that. Tap the helmet and speak again, or tap Type.")
        self.assertEqual(app.PENDING_SEND["request"], "reply with hello")
        controls = (["[1] button 'Send' at (600, 500)"], {1: (1200, 1000)})
        result, _bridge, gemini, *_ = self.run_task("send it", [plan([{"type": "click_element", "id": 1}], "Sent!")],
                                                    controls=controls)
        self.assertEqual(result, "Sent!")
        self.assertIn('the final send/post/run step you held back: "reply with hello"', gemini.call_args.args[0][-1]["text"])

    def test_declined_risky_action_stops_the_task_before_it_runs(self):
        actions = [
            {"type": "press_key", "key": "tab"},
            {"type": "delete_file", "path": r"C:\Users\Test\old-notes.txt"},
            {"type": "press_key", "key": "enter"},
        ]
        result, _bridge, gemini, run, ask, _capture = self.run_task("Clean up old notes", [plan(actions)], confirm=[False])
        self.assertEqual(result, "Stopped: you didn't allow \"Delete C:\\Users\\Test\\old-notes.txt?\" Done before that: ran press_key.")
        ask.assert_called_once_with(mock.ANY, r"Delete C:\Users\Test\old-notes.txt?")
        self.assertEqual([call.args[0]["type"] for call in run.call_args_list], ["press_key"])
        self.assertEqual(gemini.call_count, 1)

    def test_no_answer_counts_as_no_and_says_so(self):
        action = {"type": "system_control", "command": "lock_pc"}
        result, _bridge, _gemini, run, _ask, _capture = self.run_task("Lock up", [plan([action])], confirm=[None])
        self.assertEqual(result, "Stopped: no answer in time for \"Lock the PC?\"")
        run.assert_not_called()

    def test_allowed_risky_action_runs_and_the_task_continues(self):
        delete = {"type": "delete_file", "path": r"C:\Users\Test\old-notes.txt"}
        plans = [plan([delete]), plan([], "Deleted the old notes.", done=True)]
        result, _bridge, _gemini, run, ask, _capture = self.run_task("Clean up old notes", plans, confirm=[True])
        self.assertEqual(result, "Deleted the old notes.")
        ask.assert_called_once()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], delete)

    def test_a_busy_gemini_is_retried_and_then_reported_and_the_overlay_returns(self):
        result, bridge, gemini, run, _ask, _capture = self.run_task(
            "Do a harmless test", RuntimeError("Gemini (gemini-3.5-flash) answered HTTP 503. High demand."))
        self.assertEqual(result, "Stopped after 3 failed tries in a row. "
                                 "Last problem: Gemini (gemini-3.5-flash) answered HTTP 503. High demand.")
        self.assertEqual(gemini.call_count, 3)
        self.assertEqual(len(bridge.screen_show_sig.values), 1)
        self.assertEqual(bridge.status_sig.values[-1], ("Status: Ready!",))
        run.assert_not_called()

    def test_gemini_recovering_after_a_busy_answer_finishes_the_task(self):
        plans = [RuntimeError("HTTP 503"), plan([], "Done after a retry.", done=True)]
        result, *_ = self.run_task("Do a harmless test", plans)
        self.assertEqual(result, "Done after a retry.")

    def test_total_task_deadline_stops_before_making_an_api_request(self):
        with mock.patch.object(app, "MAX_TASK_SECONDS", -1):
            result, _bridge, gemini, _run, _ask, _capture = self.run_task("Do a harmless test", [])
        self.assertEqual(result, "Stopped at Motorcycle Man's time or step limit. Nothing was done.")
        gemini.assert_not_called()

    def test_keys_left_held_down_are_released_when_a_task_ends(self):
        with mock.patch.object(app.Win32Input, "held_keys", {"shift"}), \
                mock.patch.object(app.Win32Input, "key_up") as key_up:
            self.run_task("Select some text", [plan([], "Nothing to do.")])
        key_up.assert_called_once_with("shift")

    RESEARCH_CARD = {
        "title": "Claude's Redesigned Projects", "takeaway": "Projects are now conversation-driven workspaces.",
        "summary": ["Projects hold chats, files and instructions."], "report": "Projects changed.[1]",
        "sources": [{"title": "What are projects?", "url": "https://support.claude.com/p"},
                    {"title": "Projects redesigned", "url": "https://claude.com/r"}],
        "question": "Claude's new Projects feature", "engine": "DuckDuckGo", "model": "gemini-flash-lite-latest",
    }

    def fake_research(self, question, key, progress):
        progress("Searching the web...")
        return dict(self.RESEARCH_CARD, question=question)

    def test_a_plain_research_request_shows_the_card_without_looking_at_the_screen(self):
        result, bridge, gemini, run, _ask, capture = self.run_task(
            "Do a little research for me about Claude's new Projects feature.", [], research=self.fake_research)
        self.assertEqual(result, "Projects are now conversation-driven workspaces.")
        gemini.assert_not_called()
        capture.assert_not_called()
        run.assert_not_called()
        self.assertEqual(self.research.call_args.args[:2], ("Claude's new Projects feature", "unit-test-key"))
        self.assertEqual(bridge.research_start_sig.values, [("Claude's new Projects feature",)])
        self.assertEqual(bridge.research_step_sig.values, [("Searching the web...",)])
        self.assertIn(("Status: Searching the web...",), bridge.status_sig.values)
        self.assertEqual(bridge.research_done_sig.values[0][0]["title"], "Claude's Redesigned Projects")
        self.assertEqual(bridge.say_sig.values, [("Doing some research...", 0), (result, 9000)])
        self.assertEqual(self.memory_after[-1]["result"], 'Showed a research card, "Claude\'s Redesigned Projects": '
                                                          "Projects are now conversation-driven workspaces.")
        self.assertEqual(app.LAST_RESEARCH["sources"][1]["url"], "https://claude.com/r")

    def test_gemini_can_point_at_something_and_then_research_it(self):
        controls = (["[1] link 'Projects' at (100, 200)"], {1: (200, 400)})
        plans = [plan([{"type": "point_at", "id": 1, "say": "Projects is here"},
                       {"type": "research", "question": "What is Claude's new Projects feature?", "say": "Researching Projects"},
                       {"type": "click", "x": 5, "y": 5}])]
        with mock.patch.object(app, "BUDDY_HIDDEN_FROM_SCREENSHOTS", True):
            result, bridge, gemini, run, *_ = self.run_task(
                "Look at the screen and do a little research for me about this", plans, controls=controls,
                research=self.fake_research)
        self.assertEqual(result, "Projects are now conversation-driven workspaces.")
        self.assertEqual(gemini.call_count, 1)
        self.assertEqual([call.args[0]["type"] for call in run.call_args_list], ["point_at"])  # nothing runs after research
        self.assertEqual(self.research.call_args.args[0], "What is Claude's new Projects feature?")
        self.assertEqual(bridge.pointer_sig.values, [(200, 400, "Projects is here", False)])
        self.assertIn(("Researching Projects", 0), bridge.say_sig.values)

    def test_a_failed_research_shows_on_the_card_and_in_the_reply(self):
        result, bridge, *_ = self.run_task("research the tallest building in europe", [],
                                           research=RuntimeError("Gemini (gemini-3.8-flash) answered HTTP 503. Busy."))
        self.assertEqual(result, "I couldn't finish the research. Gemini (gemini-3.8-flash) answered HTTP 503. Busy.")
        self.assertEqual(bridge.research_done_sig.values, [({"question": "the tallest building in europe", "error": result},)])
        self.assertEqual(app.LAST_RESEARCH, {})

    def test_close_the_card_hides_it_without_asking_gemini(self):
        for command in ("close the card", "Hide the research card.", "dismiss research"):
            with self.subTest(command=command):
                result, bridge, gemini, *_ = self.run_task(command, [])
                self.assertEqual(result, "Closed the research card.")
                self.assertEqual(bridge.research_hide_sig.values, [()])
                gemini.assert_not_called()

    def test_the_history_keeps_each_request_with_its_reply_and_research_card(self):
        version = app.get_task_status()["history"]
        self.run_task("open the top email", [plan([], "Opened Alice's email.", done=True)])
        self.assertEqual([(e["request"], e["reply"], "card" in e) for e in self.history_after],
                         [("open the top email", "Opened Alice's email.", False)])
        self.run_task("Do a little research for me about Claude's new Projects feature.", [], research=self.fake_research)
        entry = self.history_after[-1]
        self.assertEqual((entry["request"], entry["reply"]), ("Do a little research for me about Claude's new Projects "
                                                              "feature.", "Projects are now conversation-driven workspaces."))
        self.assertEqual(entry["card"]["title"], "Claude's Redesigned Projects")
        self.assertEqual(entry["card"]["sources"][1]["url"], "https://claude.com/r")
        self.assertEqual(app.get_task_status()["history"], version + 2)  # the phone sees there's something new
        # Nothing heard, nothing kept.
        self.run_task("", [], wav=bytes(100))
        self.assertEqual(self.history_after, [])

    def test_follow_ups_can_open_a_source_from_the_last_research_card(self):
        app.LAST_RESEARCH.update(self.RESEARCH_CARD, time=time.time())
        _result, _bridge, gemini, *_ = self.run_task("open the second source", [plan([], "Opened it.", done=True)])
        prompt = gemini.call_args.args[0][-1]["text"]
        self.assertIn("Last research card (may still be open at the top right):", prompt)
        self.assertIn("[2] Projects redesigned https://claude.com/r", prompt)


BING_FEED = """<?xml version="1.0" encoding="utf-8" ?><rss version="2.0"><channel><title>Bing: claude projects</title>
<item><title>Projects redesigned &amp; more | Claude</title><link>https://claude.com/resources/articles/projects-redesigned</link>
<description>From folder to conversation.</description></item>
<item><title>Release notes</title><link>https://support.claude.com/en/articles/12138966-release-notes</link></item>
</channel></rss>"""


def http_answer(body):
    answer = mock.MagicMock()
    answer.__enter__.return_value.read.return_value = body.encode("utf-8") if isinstance(body, str) else body
    return answer


def gemini_answer(text, read=(), failed=(), supports=()):
    # A Gemini answer as the URL context tool returns it: which pages it read, and which page backs each passage.
    chunks = [{"web": {"uri": url, "title": title}} for title, url in read]
    candidate = {
        "content": {"parts": [{"text": text}]}, "finishReason": "STOP",
        "urlContextMetadata": {"urlMetadata": [{"retrievedUrl": url, "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_SUCCESS"}
                                               for _title, url in read]
                               + [{"retrievedUrl": url, "urlRetrievalStatus": "URL_RETRIEVAL_STATUS_ERROR"} for url in failed]},
        "groundingMetadata": {"groundingChunks": chunks, "groundingSupports": [
            {"segment": {"text": passage}, "groundingChunkIndices": list(indices)} for passage, indices in supports]},
    }
    return http_answer(json.dumps({"candidates": [candidate]}))


ANSWER_TEXT = """**TITLE:** Claude's Redesigned Projects
**TAKEAWAY:** Projects are now conversation-driven workspaces.
SUMMARY:
- Projects hold chats, files and instructions.
* Claude runs **parallel** threads.
1. Shared memory keeps context.
REPORT:
Projects are self-contained workspaces with their own chats and files [3].

The redesign lets Claude coordinate parallel threads from one conversation.
SUMMARY: this repeated label stays in the report."""


class ResearchTests(unittest.TestCase):
    def test_plain_research_requests_are_recognized(self):
        cases = {
            "research Claude's new Projects feature": "Claude's new Projects feature",
            "Do a little research for me about Claude's new Projects feature.": "Claude's new Projects feature",
            "please look up the tallest building in Europe": "the tallest building in Europe",
            "find out who won the Champions League final": "who won the Champions League final",
            "Hey Motorcycle Man, can you look into cheap flights to Rome?": "cheap flights to Rome",
        }
        for command, question in cases.items():
            with self.subTest(command=command):
                self.assertEqual(app.research_request(command), question)
        # About the screen or an earlier request, with more steps, or not research at all: the planner decides.
        for command in ("research this", "look up what's on my screen", "look up this word", "find out more about it",
                        "research cats and then tweet it", "research flights and open spotify",
                        "open chrome and research cats", "open the top email", "what's the weather"):
            with self.subTest(command=command):
                self.assertIsNone(app.research_request(command))

    def test_web_search_reads_bing_and_says_who_is_asking(self):
        with mock.patch.object(app.urllib.request, "urlopen", return_value=http_answer(BING_FEED)) as urlopen:
            engine, results = app.web_search("claude projects")
        self.assertEqual(engine, "Bing")
        self.assertEqual(results[0], ("Projects redesigned & more | Claude",
                                      "https://claude.com/resources/articles/projects-redesigned",
                                      "From folder to conversation."))
        request = urlopen.call_args.args[0]
        self.assertIn("format=rss", request.full_url)
        self.assertIn("MotorcycleMan", request.get_header("User-agent"))  # it doesn't pass itself off as a browser
        self.assertNotIn("Chrome", request.get_header("User-agent"))
        for failed in (app.urllib.error.URLError("offline"), http_answer("<html>no feed</html>")):
            with self.subTest(failed=failed), mock.patch.object(app.urllib.request, "urlopen", side_effect=[failed]):
                self.assertEqual(app.web_search("claude projects"), ("", []))

    def test_geminis_answer_is_split_into_the_cards_parts(self):
        card = app.parse_research_answer(ANSWER_TEXT, "claude projects")
        self.assertEqual(card["title"], "Claude's Redesigned Projects")
        self.assertEqual(card["takeaway"], "Projects are now conversation-driven workspaces.")
        self.assertEqual(card["summary"], ["Projects hold chats, files and instructions.", "Claude runs parallel threads.",
                                           "Shared memory keeps context."])
        self.assertTrue(card["report"].startswith("Projects are self-contained workspaces"))
        self.assertTrue(card["report"].endswith("SUMMARY: this repeated label stays in the report."))
        # When Gemini ignores the format, the answer becomes the report and its first sentence the takeaway.
        card = app.parse_research_answer("Spain won Euro 2024. They beat England 2-1 in the final.", "who won euro 2024")
        self.assertEqual((card["title"], card["takeaway"], card["summary"]), ("Who won euro 2024", "Spain won Euro 2024.", []))
        self.assertEqual(card["report"], "Spain won Euro 2024. They beat England 2-1 in the final.")

    def test_citations_follow_geminis_notes_on_which_page_says_what(self):
        grounding = {
            "groundingChunks": [{"web": {"uri": "https://a.example/page/"}}, {"web": {"uri": "https://b.example/x"}},
                                {"web": {"uri": "https://unknown.example/"}}],
            "groundingSupports": [
                {"segment": {"text": "Projects are workspaces."}, "groundingChunkIndices": [0]},
                {"segment": {"text": "They run threads in parallel."}, "groundingChunkIndices": [1, 0]},
                {"segment": {"text": "This sentence is not in the report."}, "groundingChunkIndices": [0]},
                {"segment": {"text": "Only an unknown page says this."}, "groundingChunkIndices": [2, 7]},
            ]}
        report = "Projects are workspaces. They run threads in parallel. Only an unknown page says this."
        numbers = {"https://a.example/page": 1, "https://b.example/x": 2}
        self.assertEqual(app.add_citations(report, grounding, numbers),
                         "Projects are workspaces.[1] They run threads in parallel.[1][2] Only an unknown page says this.")

    def run_research(self, results, answers, memory=(), question="Claude's new Projects feature"):
        progress = []
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, {"APPDATA": temp_dir}), \
                mock.patch.object(app, "web_search", return_value=results) as self.web_search, \
                mock.patch.object(app.urllib.request, "urlopen", side_effect=answers) as urlopen, \
                mock.patch.object(app.time, "sleep"):
            for command, result in memory:
                app.remember(command, result)
            card = app.research(question, "unit-test-key", progress.append)
        return card, urlopen, progress

    def test_research_reads_the_top_pages_and_fills_in_the_card(self):
        results = ("DuckDuckGo", [("What are projects?", "https://support.claude.com/p", "Projects are workspaces."),
                                  ("A video", "https://www.youtube.com/watch?v=1", ""),
                                  ("Projects redesigned", "https://claude.com/r", ""),
                                  ("Paywalled news", "https://news.example/p", "")])
        answer = gemini_answer(
            ANSWER_TEXT, read=[("What are projects? | Claude Help Center", "https://support.claude.com/p"),
                               ("Projects redesigned | Claude", "https://claude.com/r/")],
            failed=["https://news.example/p"],
            supports=[("Projects are self-contained workspaces with their own chats and files", [0]),
                      ("lets Claude coordinate parallel threads from one conversation.", [1])])
        card, urlopen, progress = self.run_research(results, [answer], memory=[("open claude", "Opened Claude.")])
        request = urlopen.call_args.args[0]
        self.assertIn(app.RESEARCH_MODELS[0], request.full_url)
        payload = json.loads(request.data)
        self.assertEqual(payload["tools"], [{"url_context": {}}])
        prompt = payload["contents"][0]["parts"][0]["text"]
        self.assertIn('The user asked Motorcycle Man to research: "Claude\'s new Projects feature"', prompt)
        self.assertIn("- What are projects?: https://support.claude.com/p\n  Projects are workspaces.\n"
                      "- Projects redesigned: https://claude.com/r\n", prompt)  # with what the search showed
        self.assertNotIn("youtube.com", prompt)  # videos can't be read as pages
        self.assertIn('the user said "open claude"; result: Opened Claude.', prompt)
        self.assertEqual(progress, ["Searching the web...", "Reading the top results..."])
        self.web_search.assert_called_once_with("Claude's new Projects feature")
        self.assertEqual(card["sources"], [  # only pages Gemini read, titled as Gemini saw them
            {"title": "What are projects? | Claude Help Center", "url": "https://support.claude.com/p"},
            {"title": "Projects redesigned | Claude", "url": "https://claude.com/r"}])
        self.assertTrue(card["report"].startswith(
            "Projects are self-contained workspaces with their own chats and files[1]."))  # Gemini's own [3] is gone
        self.assertIn("from one conversation.[2]", card["report"])
        self.assertEqual((card["engine"], card["model"], card["question"]),
                         ("DuckDuckGo", app.RESEARCH_MODELS[0], "Claude's new Projects feature"))
        self.assertNotIn("note", card)

    def test_without_search_results_gemini_answers_from_its_own_knowledge(self):
        answer = http_answer(json.dumps({"candidates": [{"content": {"parts": [{"text": ANSWER_TEXT}]}}]}))
        card, urlopen, progress = self.run_research(("", []), [answer])
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertNotIn("tools", payload)
        self.assertIn("answer from your own knowledge", payload["contents"][0]["parts"][0]["text"])
        self.assertEqual(progress, ["Searching the web...", "Thinking it over..."])
        self.assertEqual(card["sources"], [])
        self.assertEqual(card["note"], "The web search didn't work this time, so this comes from Gemini's own knowledge.")

    def test_an_answer_from_the_search_summaries_lists_the_search_results(self):
        results = ("DuckDuckGo", [(f"Result {n}", f"https://site{n}.example/a", f"Summary {n}") for n in range(1, 8)])
        answer = http_answer(json.dumps({"candidates": [{"content": {"parts": [{"text": ANSWER_TEXT}]}}]}))
        card, _urlopen, _progress = self.run_research(results, [answer])
        self.assertEqual(card["sources"], [{"title": f"Result {n}", "url": f"https://site{n}.example/a"} for n in range(1, 6)])
        self.assertEqual(card["note"], "Gemini answered from the search results' summaries without opening the pages.")

    def test_a_busy_research_model_hands_over_to_the_next_one(self):
        busy = HTTPError("https://example.invalid", 503, "busy", {}, io.BytesIO(b'{"error": {"message": "High demand"}}'))
        answer = gemini_answer(ANSWER_TEXT, read=[("Docs", "https://support.claude.com/p")])
        card, urlopen, _progress = self.run_research(("Bing", [("Docs", "https://support.claude.com/p", "")]),
                                                     [busy, busy, answer])
        models = [call.args[0].full_url.split("/models/")[1].split(":")[0] for call in urlopen.call_args_list]
        self.assertEqual(models, [app.RESEARCH_MODELS[0], app.RESEARCH_MODELS[0], app.RESEARCH_MODELS[1]])
        self.assertEqual(card["model"], app.RESEARCH_MODELS[1])

    def test_questions_about_the_newest_thing_search_with_this_year(self):
        answer = http_answer(json.dumps({"candidates": [{"content": {"parts": [{"text": ANSWER_TEXT}]}}]}))
        year = time.strftime("%Y")
        for question, query in (("who won the last Champions League final", f"who won the last Champions League final {year}"),
                                ("the latest iPhone", f"the latest iPhone {year}"),
                                ("who won the 2024 final", "who won the 2024 final"),
                                ("how tall is the Eiffel Tower", "how tall is the Eiffel Tower")):
            with self.subTest(question=question):
                self.run_research(("", []), [answer], question=question)
                self.web_search.assert_called_once_with(query)

    def test_the_card_links_citations_to_sources_and_shows_web_text_as_plain_words(self):
        card = {"takeaway": "Use *this* [now]", "summary": ["A <b>bold</b> claim"],
                "report": "Fact one.[1] Fact two.[2][9]",
                "sources": [{"title": "Docs [beta]", "url": "https://a.example/x (1)"},
                            {"title": "B", "url": "https://www.b.example/"}]}
        short = app.research_card_markdown(card, expanded=False)
        self.assertIn("**Use \\*this\\* \\[now\\]**", short)
        self.assertIn("- A \\<b\\>bold\\</b\\> claim", short)
        self.assertIn("(card:full)", short)
        full = app.research_card_markdown(card, expanded=True)
        self.assertIn("Fact one.[\\[1\\]](https://a.example/x%20%281%29) Fact two.[\\[2\\]](https://www.b.example/)\n", full)
        self.assertIn("1. [Docs \\[beta\\]](https://a.example/x%20%281%29) · a.example", full)
        self.assertIn("2. [B](https://www.b.example/) · b.example", full)
        self.assertIn("(card:less)", full)


class ConnectionAndBuddyTests(unittest.TestCase):
    def test_the_phone_link_stays_the_same_after_a_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, {"APPDATA": temp_dir}):
            first = app.phone_link_token()
            self.assertGreaterEqual(len(first), 16)
            self.assertEqual(app.phone_link_token(), first)
            with open(os.path.join(temp_dir, "Motorcycle Man", "phone.json"), "w", encoding="utf-8") as handle:
                handle.write("not json")
            replaced = app.phone_link_token()
            self.assertNotEqual(replaced, first)
            self.assertEqual(app.phone_link_token(), replaced)
        with mock.patch.object(app, "PHONE_SESSION_TOKEN", "saved-phone-token"):
            self.assertTrue(app.phone_request_is_authorized("token=saved-phone-token"))
            self.assertFalse(app.phone_request_is_authorized("token=an-old-token"))

    def test_the_pc_stays_awake_only_while_the_phone_is_connected(self):
        with mock.patch.object(app._kernel32_power, "SetThreadExecutionState") as power, \
                mock.patch.dict(app.PHONE_CONTACT, {"time": time.monotonic()}):
            self.assertTrue(app.update_keep_awake(False))
            self.assertTrue(app.update_keep_awake(True))  # no change, no new request
            app.PHONE_CONTACT["time"] = time.monotonic() - app.PHONE_AWAY_SECONDS - 1
            self.assertFalse(app.update_keep_awake(True))
        self.assertEqual([call.args[0] for call in power.call_args_list],
                         [app.ES_CONTINUOUS | app.ES_SYSTEM_REQUIRED, app.ES_CONTINUOUS])

    def test_sending_steps_are_recognized(self):
        lines = ["[1] button 'Send' at (1, 1)", "[2] edit 'Prompt' at (1, 1)"]
        self.assertTrue(app.sends_something({"type": "click_element", "id": 1}, lines))
        self.assertTrue(app.sends_something({"type": "press_key", "key": "Return"}, lines))
        self.assertTrue(app.sends_something({"type": "hotkey", "keys": ["ctrl", "enter"]}, lines))
        self.assertTrue(app.sends_something({"type": "type_text", "text": "hi", "press_enter": True}, lines))
        self.assertFalse(app.sends_something({"type": "click_element", "id": 2}, lines))
        self.assertFalse(app.sends_something({"type": "type_text", "text": "hi"}, lines))
        self.assertFalse(app.sends_something({"type": "press_key", "key": "tab"}, lines))

    def test_the_buddy_stays_on_screen_and_arrives_before_it_acts(self):
        # Right and down from what it points at, unless that would run off the right or bottom edge.
        self.assertEqual(app.buddy_side(100, 100, 1706, 1066, 400, 90), (1, 1))
        self.assertEqual(app.buddy_side(1500, 100, 1706, 1066, 400, 90), (-1, 1))
        self.assertEqual(app.buddy_side(100, 1040, 1706, 1066, 400, 90), (1, -1))
        self.assertEqual(app.buddy_side(1500, 1040, 1706, 1066, 400, 90), (-1, -1))
        # The rider's bike faces the pointer's tip, except while it rides mostly sideways.
        self.assertEqual(app.buddy_facing(0.0, 1), -1.0)
        self.assertEqual(app.buddy_facing(0.0, -1), 1.0)
        self.assertEqual(app.buddy_facing(0.9, 1), 1.0)
        self.assertEqual(app.buddy_facing(-0.6, -1), -1.0)
        self.assertEqual(app.buddy_facing(0.1, 1), -1.0)  # mostly up or down the screen
        # Short trips are quick; no trip outlasts the half second Motorcycle Man waits before acting.
        self.assertLess(app.buddy_travel_ms(50), app.buddy_travel_ms(600))
        self.assertLessEqual(app.buddy_travel_ms(5000), 500)


class PairingTests(unittest.TestCase):
    def ask(self, bridge, code="4821", answer=None, timeout=5):
        # Runs a phone's request to connect in the background, answering on the PC once it is asked.
        result = {}
        thread = threading.Thread(target=lambda: result.update(app.pair_phone(bridge, "Pixel 8 <b>", code, timeout)))
        thread.start()
        if answer is not None:
            for _ in range(200):
                if bridge.pair_sig.values:
                    break
                time.sleep(0.01)
            self.assertTrue(app.answer_pairing(bridge.pair_sig.values[-1][0], answer))
        thread.join(5)
        return result

    def test_a_phone_looking_for_the_pc_learns_nothing_secret(self):
        with mock.patch.object(app, "PHONE_SESSION_TOKEN", "secret-phone-token"):
            info = app.hello_info()
        self.assertEqual((info["app"], info["version"]), ("motorcycle-man", 2))  # 2: it takes a code typed on the phone
        self.assertNotIn("secret-phone-token", json.dumps(info))

    def test_only_allow_on_the_pc_hands_the_phone_its_link(self):
        with mock.patch.object(app, "PHONE_SESSION_TOKEN", "secret-phone-token"), mock.patch.dict(app.PAIR_REST, {"until": 0.0}):
            bridge = FakeBridge()
            self.assertEqual(self.ask(bridge, answer=True), {"token": "secret-phone-token"})
            _request_id, device, code, pc_code, through = bridge.pair_sig.values[0]
            self.assertEqual((device, code, through), ("Pixel 8 b", "4821", ""))  # shown as plain words on the PC
            self.assertRegex(pc_code, r"^\d{6}$")
            self.assertEqual(bridge.notice_sig.values, [("Pixel 8 b is connected!", 5000)])
            self.assertEqual(bridge.pair_done_sig.values, [(bridge.pair_sig.values[0][0],)])
            self.assertEqual(self.ask(FakeBridge(), answer=False), {"error": "not allowed"})
            quiet = FakeBridge()
            self.assertEqual(self.ask(quiet, timeout=0.05), {"error": "busy"})  # a rest after Don't allow
            self.assertEqual(quiet.pair_sig.values, [])
            app.PAIR_REST["until"] = 0.0
            self.assertEqual(self.ask(FakeBridge(), timeout=0.05), {"error": "not allowed"})  # nobody answered
            app.PAIR_REST["until"] = 0.0
            for code in ("", "12", "12345", "abcd"):
                with self.subTest(code=code):
                    quiet = FakeBridge()
                    self.assertEqual(self.ask(quiet, code=code), {"error": "bad code"})
                    self.assertEqual(quiet.pair_sig.values, [])  # no question for a malformed request

    def test_one_phone_asks_at_a_time(self):
        with mock.patch.object(app, "PHONE_SESSION_TOKEN", "secret-phone-token"):
            first = FakeBridge()
            result = {}
            thread = threading.Thread(target=lambda: result.update(app.pair_phone(first, "Phone one", "1111", 5)))
            thread.start()
            for _ in range(200):
                if first.pair_sig.values:
                    break
                time.sleep(0.01)
            self.assertEqual(app.pair_phone(FakeBridge(), "Phone two", "2222", 5), {"error": "busy"})
            self.assertFalse(app.answer_pairing("not-the-question", True))
            app.answer_pairing(first.pair_sig.values[0][0], True)
            thread.join(5)
            self.assertEqual(result, {"token": "secret-phone-token"})

    def start_asking(self, bridge, client, code="4821"):
        result = {}
        thread = threading.Thread(target=lambda: result.update(app.pair_phone(bridge, "Pixel 8", code, 5, client=client)))
        thread.start()
        for _ in range(200):
            if bridge.pair_sig.values:
                break
            time.sleep(0.01)
        return thread, result, bridge.pair_sig.values[0][3]

    def test_the_code_on_the_pc_can_be_typed_on_the_phone_instead_of_allow(self):
        with mock.patch.object(app, "PHONE_SESSION_TOKEN", "secret-phone-token"), \
                mock.patch.dict(app.PAIR_REST, {"until": 0.0}):
            bridge = FakeBridge()
            thread, result, pc_code = self.start_asking(bridge, "192.168.1.5")
            wrong = f"{(int(pc_code) + 1) % 10 ** 6:06d}"
            # Only the phone that asked can answer: the same address, with its own code.
            self.assertEqual(app.answer_pairing_with_code("4821", pc_code, "192.168.1.9"), {"error": "no request"})
            self.assertEqual(app.answer_pairing_with_code("1111", pc_code, "192.168.1.5"), {"error": "no request"})
            self.assertEqual(app.answer_pairing_with_code("4821", wrong, "192.168.1.5"), {"error": "wrong code", "tries_left": 2})
            self.assertEqual(app.answer_pairing_with_code("4821", pc_code[:3] + " " + pc_code[3:], "192.168.1.5"), {"ok": True})
            thread.join(5)
            self.assertEqual(result, {"token": "secret-phone-token"})  # the link still goes back to the phone that asked
            self.assertEqual(bridge.pair_done_sig.values, [(bridge.pair_sig.values[0][0],)])  # the PC's question closes
            self.assertEqual(app.answer_pairing_with_code("4821", pc_code, "192.168.1.5"), {"error": "no request"})

    def test_wrong_codes_turn_the_phone_down_and_phones_wait_a_while(self):
        with mock.patch.object(app, "PHONE_SESSION_TOKEN", "secret-phone-token"), \
                mock.patch.dict(app.PAIR_REST, {"until": 0.0}):
            thread, result, pc_code = self.start_asking(FakeBridge(), "192.168.1.5")
            wrong = f"{(int(pc_code) + 1) % 10 ** 6:06d}"
            answers = [app.answer_pairing_with_code("4821", wrong, "192.168.1.5") for _ in range(app.PAIR_CODE_TRIES)]
            self.assertEqual(answers[-1], {"error": "wrong code", "tries_left": 0})
            thread.join(5)
            self.assertEqual(result, {"error": "not allowed"})
            quiet = FakeBridge()
            self.assertEqual(app.pair_phone(quiet, "Pixel 8", "4821", 5, client="192.168.1.5"), {"error": "busy"})
            self.assertEqual(quiet.pair_sig.values, [])  # no new question on the PC for a while

    def test_a_phone_on_this_pc_connects_without_asking(self):
        with mock.patch.object(app, "PHONE_SESSION_TOKEN", "secret-phone-token"):
            bridge = FakeBridge()
            self.assertEqual(app.pair_phone(bridge, "BlueStacks", "4821", 5, client="127.0.0.1", same_pc=True),
                             {"token": "secret-phone-token"})
            self.assertEqual(bridge.pair_sig.values, [])
            self.assertEqual(bridge.notice_sig.values, [("BlueStacks is connected!", 5000)])
            self.assertEqual(bridge.act_sig.values, [("connected", {})])

    def test_only_programs_on_this_pc_that_name_it_as_this_pc_count(self):
        with mock.patch.object(app, "connection_owner", return_value=os.getpid()) as owner:
            for host in ("10.0.2.2:8080", "localhost:8080", "127.0.0.1:8080"):  # BlueStacks, a browser on the PC
                with self.subTest(host=host):
                    self.assertTrue(app.request_from_this_pc("127.0.0.1", 50123, {"Host": host}))
            self.assertEqual(owner.call_args.args, ("127.0.0.1", 50123, app.PORT))
            for client, headers in (
                    ("127.0.0.1", {"Host": "evil.example:8080"}),  # a web page that pointed its own address here
                    ("127.0.0.1", {"Host": "192.168.1.20:8080"}),  # passed on by something on this PC, from the Wi-Fi
                    ("127.0.0.1", {"Host": "localhost:8080", "X-Forwarded-For": "203.0.113.7"}),  # a tunnel
                    ("192.168.1.5", {"Host": "192.168.1.20:8080"}),  # a phone on the Wi-Fi
                    ("127.0.0.1", {})):
                with self.subTest(client=client, headers=headers):
                    self.assertFalse(app.request_from_this_pc(client, 50123, headers))
        with mock.patch.object(app, "connection_owner", return_value=None):  # not a program Windows lists
            self.assertFalse(app.request_from_this_pc("127.0.0.1", 50123, {"Host": "10.0.2.2:8080"}))

    def test_windows_names_the_program_behind_a_connection_on_this_pc(self):
        import socket
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            with socket.create_connection(server.getsockname()) as client:
                accepted, _ = server.accept()
                with accepted:
                    owner = app.connection_owner("127.0.0.1", client.getsockname()[1], server.getsockname()[1])
        self.assertEqual(owner, os.getpid())
        self.assertTrue(app.same_windows_session(os.getpid()))

    def test_a_browser_without_the_link_gets_a_page_that_connects_it(self):
        page = app.JOIN_PAGE
        self.assertIn('<svg xmlns="http://www.w3.org/2000/svg"', page)  # Motorcycle Man's face
        self.assertIn("fetch('/pair?device=' + encodeURIComponent(device) + '&code=' + match", page)
        self.assertIn("fetch('/pair_code?match=' + match + '&code=' + digits", page)
        self.assertIn("location.replace('/?token=' + encodeURIComponent(answer.token))", page)
        self.assertNotIn("<!--FACE-->", page)
        self.assertLessEqual({"/status", "/cmd", "/hold_stop"}, app.PHONE_REQUESTS)  # those still just say 403


class QuickCommandTests(unittest.TestCase):
    # Gmail as Motorcycle Man sees it: each email is a row ("Sender , Subject , Date , ...") and a link.
    GMAIL = (["[1] button 'Compose' at (90, 200)", "[2] link 'Inbox 1,589' at (100, 300)",
              "[3] button 'Delete' at (500, 150)", "[4] button 'Send' at (600, 150)",
              "[5] row 'Kalamata noreply 3 , Toast Local - Order Received , Oct 17 , Thank you for your email' at (262, 400)",
              "[6] link 'Toast Local - Order Received - Thank you for your email' at (964, 401)",
              "[7] row 'JTech Forums , Summary , Oct 14 , A brief summary since 2026-10-07' at (262, 450)",
              "[8] link 'Summary - A brief summary since 2026-10-07' at (964, 451)"],
             {1: (90, 200), 2: (100, 300), 3: (500, 150), 4: (600, 150), 5: (262, 400), 6: (964, 401),
              7: (262, 450), 8: (964, 451)})

    def quick(self, command, controls=GMAIL):
        bridge = FakeBridge()
        with mock.patch.object(app, "window_to_work_on", return_value=1234), \
                mock.patch.object(app, "describe_window_controls", return_value=controls), \
                mock.patch.object(app, "foreground_window_kind", return_value=None), \
                mock.patch.object(app, "execute_single_action", return_value="ran") as run:
            reply = app.quick_command(command, bridge, 1920, 1080)
        return reply, [call.args[0] for call in run.call_args_list], bridge

    def test_plain_keys_happen_at_once(self):
        for command, action, reply in (
                ("go back", {"type": "hotkey", "keys": ["alt", "left"]}, "Went back."),
                ("Please refresh the page.", {"type": "press_key", "key": "f5"}, "Refreshed the page."),
                ("open a new tab", {"type": "hotkey", "keys": ["ctrl", "t"]}, "Opened a new tab."),
                ("zoom in", {"type": "hotkey", "keys": ["ctrl", "="]}, "Zoomed in."),
                ("press enter", {"type": "press_key", "key": "enter"}, "Pressed Enter.")):
            with self.subTest(command=command):
                self.assertEqual(self.quick(command)[:2], (reply, [action]))

    def test_clear_clicks_happen_at_once_on_the_right_thing(self):
        for command, clicked, reply in (
                ("open the top email", 6, "Opened the top email: “Toast Local”."),
                ("Open the second email.", 8, "Opened the second email: “Summary”."),
                ("go to the email tyhat says jtech rorums", 8, "Opened “Summary”."),  # its link, not the row
                ("click compose", 1, "Clicked “Compose”."),
                ("click on delete", 3, "Clicked “Delete”."),  # said outright
                ("open inbox", 2, "Opened “Inbox 1,589”.")):
            with self.subTest(command=command):
                reply_given, actions, bridge = self.quick(command)
                self.assertEqual((reply_given, actions), (reply, [{"type": "click_element", "id": clicked}]))
                self.assertEqual(bridge.pointer_sig.values[0][3], True)  # the buddy takes the mouse there
                self.assertEqual(bridge.act_sig.values[0][0], "click")

    def test_unclear_risky_or_longer_commands_go_to_gemini(self):
        for command in ("open the email from nobody", "open the third email", "open the top email and reply to it",
                        "click send", "delete the top email", "what's on my screen", "open the top video",
                        "open the email"):
            with self.subTest(command=command):
                self.assertEqual(self.quick(command)[:2], (None, []))
        # A button that deletes isn't pressed unless the command says so.
        controls = (["[1] button 'Delete forever' at (90, 200)"], {1: (90, 200)})
        self.assertEqual(self.quick("click forever", controls)[:2], (None, []))
        # Without Gmail's email rows there is no top email to open at once.
        controls = (["[1] link 'Alice, Lunch' at (90, 200)"], {1: (90, 200)})
        self.assertEqual(self.quick("open the top email", controls)[:2], (None, []))

    def test_planning_models_think_as_little_as_each_allows(self):
        thinking = {"model-a": "minimal"}
        refused = HTTPError("https://example.invalid", 400, "bad", {}, io.BytesIO(
            b'{"error": {"message": "Thinking level MINIMAL is not supported for this model."}}'))
        answer = http_answer(json.dumps({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}))
        with mock.patch.object(app.urllib.request, "urlopen", side_effect=[refused, answer]) as urlopen:
            got = app.ask_gemini({"generationConfig": {"temperature": 0}}, "key", lambda data, model: model,
                                 models=["model-a"], thinking=thinking)
        self.assertEqual(got, "model-a")
        bodies = [json.loads(call.args[0].data) for call in urlopen.call_args_list]
        self.assertEqual([b["generationConfig"]["thinkingConfig"] for b in bodies],
                         [{"thinkingLevel": "minimal"}, {"thinkingLevel": "low"}])
        self.assertEqual(thinking, {"model-a": "low"})  # remembered
        self.assertEqual(app.GEMINI_MODELS[0], "gemini-3.5-flash-lite")
        self.assertEqual(app.THINKING_LEVELS[app.GEMINI_MODELS[0]], "minimal")

    def test_a_tap_listens_until_the_talking_stops(self):
        quiet, talk = [40.0] * 10, [2500.0] * 12
        self.assertFalse(app.speech_ended(quiet))  # nobody has talked yet
        self.assertFalse(app.speech_ended(quiet + talk + [40.0] * 10))  # half a second of quiet: maybe a pause
        self.assertTrue(app.speech_ended(quiet + talk + [40.0] * 18))  # 0.9 s of quiet: done
        self.assertFalse(app.speech_ended(quiet + [2500.0, 2500.0] + [40.0] * 30))  # a bump, not talking
        typing = ([9000.0] + [40.0] * 2) * 20  # loud key clicks, each a moment long, for 3 s
        self.assertEqual(app.speech_runs(quiet + typing), [])
        self.assertFalse(app.speech_ended(quiet + typing + [40.0] * 30))
        self.assertEqual(app.loud_moments([200.0] * 20), [])  # a steady room, however loud
        hum = [0.0] * 10 + [314.0, 376.0, 353.0, 343.0, 615.0, 574.0, 460.0, 413.0]  # a real start: silence, then the room
        self.assertEqual(app.speech_runs(hum), [])
        self.assertTrue(app.speech_runs([0.0] * 10 + [2600.0] * 6 + [0.0] * 8))  # talking in a silent room
        recorder = app.MicRecorder()
        recorder.levels = quiet + [40.0] * 100  # six seconds of a quiet room: nothing goes to Gemini
        self.assertFalse(recorder.heard_speech)
        recorder.levels = quiet + typing  # nor typing
        self.assertFalse(recorder.heard_speech)
        recorder.levels = quiet + talk + [40.0] * 18
        self.assertTrue(recorder.heard_speech)
        self.assertEqual(round(app.sound_level(array_of([1000, -1000] * 400))), 1000)
        page = app.ANDROID_WEB_CLIENT
        for piece in ("const TAP_MS = 350;", "if (Date.now() - pressedAt < TAP_MS) {", "fetch(requestUrl('/listen_stop'))",
                      "{text: '', auto: '1'}", "afterAndroidSpeech = heardEverything;",
                      "Tap the face and talk"):
            self.assertIn(piece, page)

    def test_what_was_said_is_written_down_and_silence_is_nothing(self):
        for answer, words in (("Open the top email.", "Open the top email."), ("(none)", ""), ("00:00", ""),
                              ("[silence]", ""), ('"Scroll down a bit"', "Scroll down a bit")):
            reply = http_answer(json.dumps({"candidates": [{"content": {"parts": [{"text": answer}]}}]}))
            with self.subTest(answer=answer), mock.patch.object(app.urllib.request, "urlopen", return_value=reply) as urlopen:
                self.assertEqual(app.transcribe_speech(b"RIFF" + bytes(4000), "key"), words)
                body = json.loads(urlopen.call_args.args[0].data)
                self.assertEqual(body["contents"][0]["parts"][0]["inline_data"]["mime_type"], "audio/wav")
                self.assertIn(app.TRANSCRIBE_MODELS[0], urlopen.call_args.args[0].full_url)

    def test_from_bluestacks_it_works_in_the_app_that_was_in_front_before(self):
        windows = {1: "hd-player.exe", 2: "motorcycle man", 3: "chrome.exe"}
        with mock.patch.object(app._user32_windows, "GetForegroundWindow", return_value=1), \
                mock.patch.object(app, "window_process_name", side_effect=lambda hwnd: windows[hwnd]), \
                mock.patch.object(app, "app_windows_front_to_back", return_value=[1, 2, 3]), \
                mock.patch.object(app, "window_title", return_value="t"), \
                mock.patch.object(app, "bring_to_front", return_value=True) as front:
            self.assertEqual(app.window_to_work_on(), 3)
        front.assert_called_once_with(3)
        with mock.patch.object(app._user32_windows, "GetForegroundWindow", return_value=3), \
                mock.patch.object(app, "window_process_name", side_effect=lambda hwnd: windows[hwnd]), \
                mock.patch.object(app, "bring_to_front") as front:
            self.assertEqual(app.window_to_work_on(), 3)  # already the right one
        front.assert_not_called()


def array_of(samples):
    import array
    return array.array("h", samples).tobytes()


class InternetLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.makedirs(os.path.dirname(app.cloudflared_path()), exist_ok=True)

    def open_link(self, log_text, reuse=None, known=True, reaches=None):
        # Runs InternetLink._open with a fake tunnel program whose log says log_text, on a fake clock
        # that only moves on while Motorcycle Man waits, so giving up after 45 s takes no time. known: whether
        # the internet's DNS knows the link's name; reaches: a fake reaches_this_app.
        link, seen, now = app.InternetLink(), [], [0.0]
        link.changed.connect(lambda state, detail: seen.append((state, detail)))
        log_path = os.path.join(os.path.dirname(app.cloudflared_path()), "tunnel.log")

        def start_tunnel(command, **kwargs):
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(log_text)
            return mock.DEFAULT

        def wait(seconds):
            now[0] += seconds
        tunnel = mock.Mock(pid=4242, poll=mock.Mock(return_value=None))
        with mock.patch.object(app, "get_cloudflared", return_value=app.cloudflared_path()), \
                mock.patch.object(app.subprocess, "Popen", side_effect=start_tunnel, return_value=tunnel) as popen, \
                mock.patch.object(app.InternetLink, "_running_tunnel", return_value=reuse), \
                mock.patch.object(app, "process_name", return_value=""), \
                mock.patch.object(app, "name_is_known", return_value=known), \
                mock.patch.object(app, "reaches_this_app", reaches or mock.Mock(return_value=True)), \
                mock.patch.object(app.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(app.time, "sleep", side_effect=wait):
            link._open()
        return link, seen, popen

    def test_the_link_opens_through_cloudflare_and_is_remembered(self):
        log = ('{"message":"|  https://quiet-river-blue-moon.trycloudflare.com   |"}\n'
               '{"message":"Registered tunnel connection connIndex=0"}\n')
        link, seen, popen = self.open_link(log)
        self.assertEqual((link.state, link.address), ("on", "https://quiet-river-blue-moon.trycloudflare.com"))
        self.assertEqual(seen[0][0], "opening")
        command = popen.call_args.args[0]
        self.assertEqual(command[1:5], ["tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{app.PORT}"])
        with open(os.path.join(os.path.dirname(app.cloudflared_path()), "tunnel.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"pid": 4242, "address": "https://quiet-river-blue-moon.trycloudflare.com"})
        # An Motorcycle Man started later picks up the running link rather than opening a new one.
        link, seen, popen = self.open_link("", reuse="https://quiet-river-blue-moon.trycloudflare.com")
        self.assertEqual(link.state, "on")
        popen.assert_not_called()
        # Turning it off stops the tunnel program and forgets it.
        with mock.patch.object(app, "process_name", return_value="cloudflared.exe"), \
                mock.patch.object(app.os, "kill") as kill:
            link.turn_off()
        kill.assert_called_once_with(4242, 9)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(app.cloudflared_path()), "tunnel.json")))
        self.assertEqual((link.state, app.load_settings()["internet_link"]), ("off", False))

    def test_the_link_is_on_once_it_answers_through_it(self):
        log = ('{"message":"|  https://quiet-river-blue-moon.trycloudflare.com   |"}\n'
               '{"message":"Registered tunnel connection connIndex=0"}\n')
        reaches = mock.Mock(side_effect=[False, False, True])  # Cloudflare's own page at first
        link, seen, _popen = self.open_link(log, reaches=reaches)
        self.assertEqual((link.state, reaches.call_count), ("on", 3))
        # While the internet's DNS doesn't know the name yet, this PC doesn't look it up (Windows would
        # remember "no such name" for minutes); the link is shown anyway, for the phone.
        reaches = mock.Mock(return_value=True)
        link, seen, _popen = self.open_link(log, known=False, reaches=reaches)
        self.assertEqual(link.state, "on")
        reaches.assert_not_called()

    def test_a_saved_link_is_used_only_while_it_still_reaches_this_app(self):
        saved, link = os.path.join(os.path.dirname(app.cloudflared_path()), "tunnel.json"), app.InternetLink()
        good = "https://quiet-river-blue-moon.trycloudflare.com"
        for address, program, answers, expected in ((good, "cloudflared.exe", True, good),
                                                    (good, "cloudflared.exe", False, None),  # Motorcycle Man doesn't answer through it
                                                    (good, "notepad.exe", True, None),  # the tunnel program is gone
                                                    ("https://evil.example", "cloudflared.exe", True, None)):
            with self.subTest(address=address, program=program, answers=answers):
                with open(saved, "w", encoding="utf-8") as f:
                    json.dump({"pid": 4242, "address": address}, f)
                with mock.patch.object(app, "process_name", return_value=program), \
                        mock.patch.object(app, "reaches_this_app", return_value=answers):
                    self.assertEqual(link._running_tunnel(), expected)

    def test_a_tunnel_that_never_connects_is_reported(self):
        link, seen, popen = self.open_link('{"message":"Requesting new quick Tunnel on trycloudflare.com..."}\n')
        self.assertEqual(link.state, "failed")
        self.assertIn("didn't open a link in time", seen[-1][1])
        popen.return_value.kill.assert_called_once()  # the tunnel program isn't left running
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(app.cloudflared_path()), "tunnel.json")))

    @staticmethod
    def download_answer(data):
        # GitHub's answer to the download: data, a piece at a time, and then the end.
        answer, stream = mock.MagicMock(), io.BytesIO(data)
        answer.__enter__.return_value.read.side_effect = stream.read
        answer.__enter__.return_value.headers = {"Content-Length": str(len(data))}
        return answer

    def test_only_a_download_signed_by_cloudflare_is_kept(self):
        program = b"MZ" + bytes(3 << 20)
        for signed in (False, True):
            with self.subTest(signed=signed), \
                    mock.patch.object(app.urllib.request, "urlopen", return_value=self.download_answer(program)), \
                    mock.patch.object(app, "signed_by_cloudflare", return_value=signed):
                if signed:
                    self.assertEqual(app.get_cloudflared(), app.cloudflared_path())
                    self.assertEqual(os.path.getsize(app.cloudflared_path()), len(program))
                else:
                    with self.assertRaises(RuntimeError):
                        app.get_cloudflared()
                    self.assertFalse(os.path.exists(app.cloudflared_path() + ".part"))
                    self.assertFalse(os.path.exists(app.cloudflared_path()))
        # A download that never ends is stopped, and nothing is left behind.
        os.remove(app.cloudflared_path())
        endless = mock.MagicMock()
        endless.__enter__.return_value.read.return_value = bytes(1 << 20)
        endless.__enter__.return_value.headers = {}
        with mock.patch.object(app.urllib.request, "urlopen", return_value=endless), \
                mock.patch.object(app, "CLOUDFLARED_SIZE_LIMIT", 5 << 20), \
                mock.patch.object(app, "signed_by_cloudflare", return_value=True):
            with self.assertRaises(RuntimeError):
                app.get_cloudflared()
        self.assertEqual(endless.__enter__.return_value.read.call_count, 6)
        self.assertFalse(os.path.exists(app.cloudflared_path() + ".part"))
        self.assertFalse(os.path.exists(app.cloudflared_path()))

    def test_a_fresh_secret_and_settings_are_kept(self):
        first = app.phone_link_token()
        self.assertEqual(app.phone_link_token(), first)
        fresh = app.phone_link_token(fresh=True)
        self.assertNotEqual(fresh, first)
        self.assertEqual(app.phone_link_token(), fresh)
        app.save_setting("internet_link", True)
        self.assertEqual(app.load_settings(), {"internet_link": True})

    def test_requests_through_cloudflare_never_count_as_this_pc(self):
        with mock.patch.object(app, "connection_owner", return_value=os.getpid()):
            self.assertFalse(app.request_from_this_pc("127.0.0.1", 50123, {"Host": "localhost:8080", "Cf-Connecting-Ip": "203.0.113.7"}))
        self.assertTrue(app.TUNNEL_ADDRESS.search("Visit it at https://a-b-c.trycloudflare.com  |"))
        self.assertFalse(app.TUNNEL_ADDRESS.search("https://evil.example/?x=trycloudflare.com"))

    def test_away_from_home_the_phone_listens_itself(self):
        page = app.ANDROID_WEB_CLIENT
        for piece in ("const remote = location.protocol === 'https:';", "if (!remote) fetch(requestUrl('/hold_start'))",
                      "recognition.onend = () => { if (tapListening === 'phone') heardEverything(); };"):
            self.assertIn(piece, page)
        recorder = app.MicRecorder()
        recorder._sound = bytearray(b"\x01\x00" * 1000)
        self.assertTrue(recorder.stop().startswith(b"RIFF"))
        self.assertEqual(recorder.stop(), b"")  # handed over once


class StopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_while_working(self, then):
        # A first command works (step after step) until it is stopped; then(bridge) does something
        # meanwhile, the way a second request from the phone would. Returns the first reply, what
        # then returned, how long the first took to end after it, and the bridge.
        bridge, started, replies = FakeBridge(), threading.Event(), {}

        def fake_task(cmd, *args, **kwargs):
            if cmd == "open the second chat":
                started.set()
                while True:
                    app.task_sleep(0.05)
            return f"Did {cmd}."
        with mock.patch.object(app, "_execute_smart_action_locked", side_effect=fake_task) as task, \
                mock.patch.object(app, "wake_screen"):
            worker = threading.Thread(target=lambda: replies.update(
                first=app.execute_smart_action("open the second chat", bridge, 1000, 800)))
            worker.start()
            self.assertTrue(started.wait(5))
            began = time.monotonic()
            answer = then(bridge)
            worker.join(5)
            took = time.monotonic() - began
        self.assertFalse(worker.is_alive())
        self.task_calls = [call.args[0] for call in task.call_args_list]
        return replies["first"], answer, took, bridge

    def test_a_new_command_stops_the_running_one_and_runs(self):
        first, second, took, bridge = self.run_while_working(
            lambda bridge: app.execute_smart_action("open Gmail", bridge, 1000, 800))
        self.assertEqual((first, second), ("Stopped for your new command.", "Did open Gmail."))
        self.assertLess(took, 2)
        self.assertNotIn(("Stopped for your new command.", 4000), bridge.say_sig.values)  # the next one speaks

    def test_saying_stop_only_stops(self):
        first, second, took, _bridge = self.run_while_working(
            lambda bridge: app.execute_smart_action("okay stop", bridge, 1000, 800))
        self.assertEqual(second, "Okay, I stopped.")
        self.assertEqual(self.task_calls, ["open the second chat"])  # "stop" ran nothing itself
        self.assertLess(took, 2)

    def test_the_stop_button_stops_the_running_task(self):
        first, pressed, took, bridge = self.run_while_working(lambda bridge: app.stop_task())
        self.assertEqual((first, pressed), ("Okay, I stopped.", True))
        self.assertIn(("Okay, I stopped.", 4000), bridge.say_sig.values)
        self.assertFalse(app.stop_task())  # nothing is running now

    def test_stop_words(self):
        for text in ("stop", "Stop!", "okay stop", "cancel that", "never mind", "Nevermind.", "forget it",
                     "stop what you're doing", "Motorcycle Man, stop it", "no no stop"):
            with self.subTest(text=text):
                self.assertTrue(app.is_stop_request(text))
        for text in ("stop the video", "cancel my order", "stop sharing my screen", "", "open stopwatch"):
            with self.subTest(text=text):
                self.assertFalse(app.is_stop_request(text))

    def inside_a_task(self):
        # As if a task were running: its number is the newest, and it hasn't been stopped.
        app.TASK_STOP.clear()
        with app.TASK_TURNS_LOCK:
            app.TASK_TURNS["latest"] += 1
            app.TASK_TURNS["running"] = app.TASK_TURNS["latest"]
        self.addCleanup(app.TASK_TURNS.update, running=0)

    def test_waiting_for_gemini_ends_at_a_stop(self):
        self.inside_a_task()

        def slow_answer(request, timeout):
            time.sleep(3)
            return {}
        threading.Timer(0.3, app.TASK_STOP.set).start()
        began = time.monotonic()
        with mock.patch.object(app, "read_json_answer", side_effect=slow_answer):
            with self.assertRaises(app.TaskStopped):
                app.ask_gemini({"contents": []}, "unit-test-key", lambda data, model: data, models=["gemini-test"])
        self.assertLess(time.monotonic() - began, 1.5)

    def test_typing_stops_part_way_and_a_held_button_is_let_go(self):
        self.inside_a_task()
        app.TASK_STOP.set()
        with mock.patch.object(app.Win32Input, "send_inputs") as typed:
            with self.assertRaises(app.TaskStopped):
                app.Win32Input.type_unicode_text("x" * 40, press_enter=True)
        self.assertEqual(typed.call_count, 15)
        with mock.patch.object(app.Win32Input.user32, "mouse_event") as mouse:
            app.Win32Input.mouse_down(button="left")  # a drag, cut short
            app.Win32Input.release_held_keys()
        self.assertEqual(mouse.call_args_list[-1].args[0], 0x0004)  # left button up
        self.assertEqual(app.Win32Input.held_buttons, set())

    def test_a_slow_window_is_looked_at_without_its_controls(self):
        self.addCleanup(app.SLOW_PROGRAMS.clear)

        def slow_read(*args):
            time.sleep(0.6)
            return ["[1] button 'Chats' at (10, 10)"], {1: (10, 10)}
        with mock.patch.object(app, "describe_window_controls", side_effect=slow_read) as read, \
                mock.patch.object(app, "window_process_name", return_value="whatsapp.root.exe"), \
                mock.patch.object(app, "CONTROLS_BUDGET_SECONDS", 0.2):
            began = time.monotonic()
            self.assertEqual(app.read_window_controls(1234, 1920, 1080, 1920, 1080), ([], {}))
            self.assertLess(time.monotonic() - began, 0.5)
            self.assertEqual(app.read_window_controls(1234, 1920, 1080, 1920, 1080), ([], {}))  # not read again
        self.assertEqual(read.call_count, 1)

    def test_the_phone_has_a_stop_button_and_shows_only_the_newest_reply(self):
        page = app.ANDROID_WEB_CLIENT
        for piece in ('<button id="stopBtn" onclick="stopTask()"', "fetch(requestUrl('/stop'))",
                      "const mine = ++commandSeq;", "if (mine === commandSeq) showReply(d.reply);",
                      "(awaitingReply || busy) && !holding && !tapListening && !stopRequested);",
                      "stopRequested = true;"):
            self.assertIn(piece, page)
        self.assertIn("/stop", app.PHONE_REQUESTS)

    def test_opening_it_again_brings_the_running_one_forward(self):
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.instance() or QGuiApplication([])
        window = mock.Mock()
        with mock.patch.object(app, "instance_name", return_value=f"motorcycle-man-unit-test-{os.getpid()}"):
            self.assertFalse(app.show_running_app())  # none running: this one starts
            server = app.listen_for_another_start(window)
            try:
                self.assertTrue(app.show_running_app())  # another start: it goes away
                end = time.monotonic() + 3
                while not window.activateWindow.called and time.monotonic() < end:
                    QCoreApplication.processEvents()
                    time.sleep(0.02)
            finally:
                server.close()
        window.showNormal.assert_called_once()
        window.activateWindow.assert_called_once()


class BuddyShowTests(unittest.TestCase):
    def test_keys_and_shortcuts_read_like_keys(self):
        for action, label in (({"type": "press_key", "key": "enter"}, "Enter"),
                              ({"type": "press_key", "key": "down", "presses": 3}, "↓ ×3"),
                              ({"type": "hotkey", "keys": ["ctrl", "c"]}, "Ctrl + C"),
                              ({"type": "hotkey", "keys": "control+shift+escape"}, "Ctrl + Shift + Esc")):
            with self.subTest(action=action):
                self.assertEqual(app.key_label(action), label)

    def test_each_action_gets_its_little_show(self):
        bridge = FakeBridge()
        for action in ({"type": "double_click", "x": 100, "y": 50}, {"type": "right_click", "x": 100, "y": 50},
                       {"type": "hotkey", "keys": ["ctrl", "t"]}, {"type": "scroll", "direction": "up", "amount": 3},
                       {"type": "open_app", "app": "notepad"},
                       {"type": "drag", "start_x": 0, "start_y": 0, "end_x": 480, "end_y": 270, "duration": 0.5},
                       {"type": "wait", "seconds": 1}):
            app.act_out(bridge, action, 1920, 1080, 960, 540, point=(200, 100))
        self.assertEqual(bridge.act_sig.values, [
            ("click", {"x": 200, "y": 100, "clicks": 2, "button": "left"}),
            ("click", {"x": 200, "y": 100, "clicks": 1, "button": "right"}),
            ("key", {"label": "Ctrl + T"}),
            ("scroll", {"direction": "up", "ms": 600}),
            ("open", {}),
            ("drag", {"x": 960, "y": 540, "ms": 500}),  # the buddy runs along with the drag
        ])

    def test_every_show_draws_inside_its_window_and_is_gone_at_the_end(self):
        from PySide6.QtGui import QGuiApplication, QImage, QPainter
        QGuiApplication.instance() or QGuiApplication([])

        def paint(effect, t):
            width, height = effect.size
            image = QImage(width, height, QImage.Format.Format_ARGB32)
            image.fill(0)
            p = QPainter(image)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.translate(width / 2, height / 2)
            effect.draw(p, t)
            p.end()
            return image

        def coverage(image):
            # How much is drawn, and how much of it touches the window's edge (where it would be cut off).
            drawn = edge = 0
            for y in range(0, image.height(), 2):
                for x in range(0, image.width(), 2):
                    if image.pixelColor(x, y).alpha() > 40:
                        drawn += 1
                        edge += x < 2 or y < 2 or x >= image.width() - 2 or y >= image.height() - 2
            return drawn, edge

        shows = [app.Puff((-1, 0), count=4, along=(40, 0)), app.Puff(count=5, scale=1.05), app.Ripple(2), app.Ripple(1, True),
                 app.Keys("Hello there!"), app.KeyCap("Ctrl + Enter"), app.Wheel("down", 0.6), app.Wheel("left", 2.0),
                 app.Sparkles(12), app.Confetti(), app.Hearts(), app.Drops(), app.Backfire(-1), app.Backfire(1),
                 app.Puff((-0.9, -0.8), count=3, scale=0.42, grey=True, duration=0.8, stagger=0.11),
                 app.Puff((-0.6, -0.8), count=6, scale=0.8, grey=True, along=(60, 0), age=0.1)]
        for show in shows:
            with self.subTest(show=type(show).__name__):
                middle = coverage(paint(show, show.duration * 0.3))
                self.assertGreater(middle[0], 20)  # something to see
                self.assertLess(middle[1], 4)  # and not cut off by its window
                self.assertEqual(coverage(paint(show, show.duration))[0], 0)  # faded out when its time is up

    def test_the_task_ends_with_confetti_or_a_shake(self):
        for reply, show in (("Opened Gmail.", [("done", {})]), ("Error: no network", [("oops", {})]),
                            ("Tweet ready, say yes to post it", [])):
            bridge = FakeBridge()
            with self.subTest(reply=reply), mock.patch.object(app, "_execute_smart_action_locked", return_value=reply), \
                    mock.patch.object(app, "wake_screen"), mock.patch.object(app, "remember"), \
                    mock.patch.object(app, "add_to_history"):
                self.assertEqual(app.execute_smart_action("open gmail", bridge, 1920, 1080), reply)
                self.assertEqual(bridge.act_sig.values, show)


class HistoryTests(unittest.TestCase):
    def test_the_history_skips_empty_commands_and_keeps_only_the_newest(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, {"APPDATA": temp_dir}):
            app.add_to_history("   ", "I didn't catch that.")
            self.assertEqual(app.load_history(), [])
            for n in range(app.HISTORY_LIMIT + 5):
                app.add_to_history(f"command {n}", f"reply {n}")
            entries = app.load_history()
            self.assertEqual(len(entries), app.HISTORY_LIMIT)
            self.assertEqual((entries[0]["request"], entries[-1]["request"]), ("command 5", f"command {app.HISTORY_LIMIT + 4}"))
            self.assertEqual(len({e["id"] for e in entries}), app.HISTORY_LIMIT)

    def test_the_phone_gets_research_reports_as_safe_html(self):
        card = {
            "title": "Tall <b>", "takeaway": "Takeaway", "summary": ["Point"], "note": "",
            "report": '## Overview\nIt is **big** <script>alert(1)</script> [1][2].\nSecond line [3].\n\n- one [1]\n* two\n\n'
                      'See [docs](https://a.example/d"x) or [bad](javascript:alert(1)).',
            "sources": [{"title": "A", "url": "https://a.example/p?x=1&y=2"}, {"title": "Bad", "url": "javascript:alert(1)"},
                        {"title": "C", "url": "http://www.c.example/"}],
        }
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, {"APPDATA": temp_dir}):
            app.add_to_history("research tall things", "Takeaway", card)
            app.add_to_history("open notepad", "Opened Notepad.")
            entries = app.history_for_phone()["entries"]
        self.assertNotIn("card", entries[1])
        phone_card = entries[0]["card"]
        cite_1 = '<a href="https://a.example/p?x=1&amp;y=2" target="_blank" rel="noopener">[1]</a>'
        self.assertEqual(phone_card["report_html"],
                         "<p><b>Overview</b></p>"
                         f"<p>It is <b>big</b> &lt;script&gt;alert(1)&lt;/script&gt; {cite_1}. "
                         'Second line <a href="http://www.c.example/" target="_blank" rel="noopener">[3]</a>.</p>'
                         f"<ul><li>one {cite_1}</li><li>two</li></ul>"
                         '<p>See <a href="https://a.example/d&quot;x" target="_blank" rel="noopener">docs</a> '
                         "or [bad](javascript:alert(1)).</p>")
        # Every source keeps its number for the report's [n]; only web addresses are links.
        self.assertEqual(phone_card["sources"], [
            {"title": "A", "url": "https://a.example/p?x=1&y=2", "site": "a.example"},
            {"title": "Bad", "url": "", "site": ""},
            {"title": "C", "url": "http://www.c.example/", "site": "c.example"}])
        self.assertEqual((phone_card["title"], phone_card["summary"]), ("Tall <b>", ["Point"]))

    def test_the_phone_page_shows_the_history_with_research_cards(self):
        page = app.ANDROID_WEB_CLIENT
        for piece in ('id="historyBtn" onclick="fromMenu(openHistory)"', "fetch(requestUrl('/history')",
                      "noticeHistory(state.history);", "function researchCard(card, time)", "classList.add('fresh')"):
            self.assertIn(piece, page)
        # Requests, replies and web text go in as text; only the report, made safe on the PC, is HTML.
        self.assertEqual(page.count("innerHTML"), 1)
        self.assertIn("full.innerHTML = card.report_html;", page)
        self.assertIn("history", app.get_task_status())


def binary_manifest(version_code, version_name, utf8=False):
    # A small AndroidManifest.xml as an APK holds it, in Android's binary XML: the pool of texts, then
    # <manifest android:versionCode="..." android:versionName="...">.
    texts = ["versionCode", "versionName", "android", "http://schemas.android.com/apk/res/android", version_name,
             "manifest"]
    if utf8:
        encoded = [bytes([len(text), len(text.encode())]) + text.encode() + b"\0" for text in texts]
    else:
        encoded = [struct.pack("<H", len(text)) + text.encode("utf-16-le") + b"\0\0" for text in texts]
    offsets = [sum(map(len, encoded[:i])) for i in range(len(texts))]
    pool_texts = b"".join(encoded)
    pool_texts += b"\0" * (-len(pool_texts) % 4)
    start = 28 + 4 * len(texts)
    pool = (struct.pack("<HHIIIIII", 0x0001, 28, start + len(pool_texts), len(texts), 0, 0x100 if utf8 else 0, start, 0)
            + struct.pack(f"<{len(texts)}I", *offsets) + pool_texts)
    attributes = (struct.pack("<IIIHBBI", 3, 0, 0xFFFFFFFF, 8, 0, 0x10, version_code)  # a number
                  + struct.pack("<IIIHBBI", 3, 1, 4, 8, 0, 0x03, 4))  # text number 4
    element = (struct.pack("<HHIII", 0x0102, 16, 36 + len(attributes), 1, 0xFFFFFFFF)
               + struct.pack("<IIHHHHHH", 0xFFFFFFFF, 5, 20, 20, 2, 0, 0, 0) + attributes)
    return struct.pack("<HHI", 0x0003, 8, 8 + len(pool) + len(element)) + pool + element


def write_apk(path, manifest):
    with zipfile.ZipFile(path, "w") as apk:
        apk.writestr("AndroidManifest.xml", manifest)
        apk.writestr("classes.dex", b"dex\n035\0" + bytes(4000))


BUILT_APK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(app.__file__))), "Android", "MotorcycleMan.apk")


class RemoteButtonTests(unittest.TestCase):
    # The phone's remote: media keys, volume and brightness with their new level, lock and sleep.
    def test_volume_keys_say_the_new_level(self):
        with mock.patch.object(app.Win32Input, "press_key") as press, mock.patch.object(app.time, "sleep"), \
                mock.patch.object(app, "master_volume", return_value=(60, False)):
            self.assertEqual(app.remote_action("volume_up"), {"say": "Volume 60%", "level": 60})
            press.assert_called_once_with("volumeup", presses=app.VOLUME_STEP_PRESSES)
        with mock.patch.object(app.Win32Input, "press_key") as press, mock.patch.object(app.time, "sleep"), \
                mock.patch.object(app, "master_volume", return_value=(60, True)):
            self.assertEqual(app.remote_action("mute"), {"say": "Muted", "level": 0})
            press.assert_called_once_with("volumemute", presses=1)
        with mock.patch.object(app.Win32Input, "press_key"), mock.patch.object(app.time, "sleep"), \
                mock.patch.object(app, "master_volume", return_value=None):
            self.assertEqual(app.remote_action("volume_down"), {"say": "Quieter"})  # when Windows won't say the level

    def test_music_keys_brightness_lock_and_sleep(self):
        with mock.patch.object(app.Win32Input, "press_key") as press:
            self.assertEqual(app.remote_action("next"), {"say": "Next track"})
            press.assert_called_once_with("nexttrack", presses=1)
        with mock.patch.object(app, "screen_brightness", return_value=40) as brightness:
            self.assertEqual(app.remote_action("brightness_up"), {"say": "Brightness 40%", "level": 40})
            brightness.assert_called_once_with(app.BRIGHTNESS_STEP)
        with mock.patch.object(app, "screen_brightness", return_value=None):
            self.assertEqual(app.remote_action("brightness_down")["say"], "This screen can't be dimmed from here")
        user32 = mock.MagicMock()
        with mock.patch.object(app.ctypes, "windll", mock.MagicMock(user32=user32)):
            self.assertEqual(app.remote_action("lock"), {"say": "Locked the PC"})
        user32.LockWorkStation.assert_called_once_with()
        with mock.patch.object(app.threading, "Timer") as timer:
            self.assertIn("sleep", app.remote_action("sleep")["say"])
        timer.return_value.start.assert_called_once_with()  # after a second, so the phone hears the answer first
        self.assertEqual(app.remote_action("explode")["say"], "That button isn't known here")


class SendBetweenPhoneAndPcTests(unittest.TestCase):
    # Text to and from the PC's clipboard, links opened on the PC, and files both ways, through the real
    # phone server, with a stand-in Desktop and Downloads.
    TOKEN = "send-test-token"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.desktop, self.downloads = (os.path.join(self.temp.name, name) for name in ("Desktop", "Downloads"))
        for folder in (self.desktop, self.downloads):
            os.makedirs(folder)
        folders = {"Desktop": self.desktop, "Downloads": self.downloads, "Documents": self.temp.name}
        for patcher in (mock.patch.object(app, "PHONE_SESSION_TOKEN", self.TOKEN), mock.patch.dict(app.PHONE_CONTACT),
                        mock.patch.object(app, "get_known_folder_paths", return_value=folders)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bridge = FakeBridge()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(self.bridge, 1000, 800))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        self.inbox = os.path.join(self.downloads, "Motorcycle Man")

    def call(self, path, data=None, token=TOKEN, **params):
        # (status, headers, body) of a GET, or a POST of data, straight to the server (no proxy).
        query = urllib.parse.urlencode(dict(params, **({"token": token} if token else {})))
        request = urllib.request.Request(f"{self.base}{path}?{query}", data=data, method="GET" if data is None else "POST")
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as reply:
                return reply.status, reply.headers, reply.read()
        except HTTPError as error:
            with error:
                return error.code, error.headers, error.read()

    def test_files_from_the_phone_land_in_downloads_and_nothing_is_overwritten(self):
        self.assertEqual(self.call("/upload", b"abc", token=None, name="photo.jpg")[0], 403)
        self.assertEqual(self.call("/upload", b"abc", name="photo.jpg")[0], 200)
        status, _, body = self.call("/upload", b"second", name="photo.jpg", n=2, of=2)
        self.assertEqual((status, json.loads(body)["name"]), (200, "photo (1).jpg"))
        with open(os.path.join(self.inbox, "photo.jpg"), "rb") as handle:
            self.assertEqual(handle.read(), b"abc")
        with open(os.path.join(self.inbox, "photo (1).jpg"), "rb") as handle:
            self.assertEqual(handle.read(), b"second")
        self.assertIn("Got 2 files from your phone", self.bridge.notice_sig.values[-1][0])  # the rider says so
        self.assertIn(("open", {}), self.bridge.act_sig.values)
        self.call("/upload", b"x", name="..\\..\\sneaky.txt")
        self.assertEqual(sorted(os.listdir(self.inbox)), ["photo (1).jpg", "photo.jpg", "sneaky.txt"])  # never outside
        with self.assertRaises(ConnectionError):  # the phone stopped sending: nothing half-written is left
            app.receive_phone_file(io.BytesIO(b"ab"), 5, "cut.bin")
        with mock.patch.object(app, "UPLOAD_LIMIT", 3), self.assertRaises(ValueError):
            app.receive_phone_file(io.BytesIO(b"abcd"), 4, "big.bin")
        self.assertEqual(sorted(os.listdir(self.inbox)), ["photo (1).jpg", "photo.jpg", "sneaky.txt"])

    def test_the_phone_saves_the_newest_pc_files_named_by_id(self):
        for folder, name, age in ((self.desktop, "old.txt", 300), (self.desktop, "new.pdf", 10), (self.downloads, "pic.png", 100),
                                  (self.desktop, "shortcut.lnk", 1)):
            path = os.path.join(folder, name)
            with open(path, "wb") as handle:
                handle.write(name.encode())
            os.utime(path, (time.time() - age, time.time() - age))
        os.makedirs(os.path.join(self.desktop, "a folder"))
        status, _, body = self.call("/files")
        files = json.loads(body)["files"]
        self.assertEqual([(f["name"], f["where"]) for f in files], [("new.pdf", "Desktop"), ("pic.png", "Downloads"), ("old.txt", "Desktop")])
        status, headers, body = self.call("/file", id=files[0]["id"])
        self.assertEqual((status, body), (200, b"new.pdf"))
        self.assertIn('attachment; filename="new.pdf"', headers["Content-Disposition"])
        for wrong in ("made-up", os.path.join(self.desktop, "old.txt")):
            self.assertEqual(self.call("/file", id=wrong)[0], 404)  # only ids from a list, never paths
        self.assertEqual(self.call("/files", token=None)[0], 403)

    def test_text_goes_to_and_from_the_pc_clipboard_and_links_open_on_the_pc(self):
        with mock.patch.object(app, "set_clipboard_text", return_value=True) as put:
            status, _, body = self.call("/clipboard", "héllo ✓".encode("utf-8"))
        put.assert_called_once_with("héllo ✓")
        self.assertIn("Ctrl+V", json.loads(body)["say"])
        with mock.patch.object(app, "clipboard_text", return_value="copied on the PC"):
            self.assertEqual(json.loads(self.call("/clipboard")[2]), {"text": "copied on the PC"})
        with mock.patch.object(app.webbrowser, "open") as browser:
            self.assertEqual(json.loads(self.call("/open_link", url="https://example.com/a")[2])["say"], "Opened it on the PC.")
            self.assertEqual(json.loads(self.call("/open_link", url="file:///C:/Windows")[2])["say"], "That isn't a web link.")
        browser.assert_called_once_with("https://example.com/a")
        page = app.ANDROID_WEB_CLIENT
        for part in ('id="sendModal"', "sendFiles(this.files)", "getPcClipboard()", "loadPcFiles()", "MotorcycleManAndroid.copyText"):
            self.assertIn(part, page)


class ScreenViewTests(unittest.TestCase):
    # The PC's screen on the phone, and the phone's taps, presses, scrolls, keys and typing done there.
    TOKEN = "screen-test-token"

    def setUp(self):
        for patcher in (mock.patch.object(app, "PHONE_SESSION_TOKEN", self.TOKEN), mock.patch.dict(app.PHONE_CONTACT)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def serve(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(FakeBridge(), 2560, 1600))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_pictures_of_the_whole_screen_or_a_part_of_it(self):
        from PIL import Image
        screen = Image.new("RGB", (2560, 1600), (240, 240, 240))
        screen.paste((200, 30, 30), (1280, 800, 2560, 1600))  # the bottom-right quarter is red
        picture, info = app.screen_picture((0, 0, 1, 1), 1280, 60, shot=screen)
        self.assertEqual((Image.open(io.BytesIO(picture)).size, info), ((1280, 800), {"region": (0, 0, 1, 1), "screen": (2560, 1600)}))
        picture, info = app.screen_picture((0.5, 0.5, 1, 1), 5000, 85, shot=screen)  # zoomed in: as sharp as the screen
        close = Image.open(io.BytesIO(picture))
        self.assertEqual((close.size, info["region"]), ((1280, 800), (0.5, 0.5, 1.0, 1.0)))
        r, g, b = close.getpixel((640, 400))
        self.assertTrue(r > 150 and g < 80, (r, g, b))
        picture, _ = app.screen_picture((0, 0, 1, 1), 10, 40, shot=screen)
        self.assertEqual(Image.open(io.BytesIO(picture)).size[0], 160)  # never too small to see
        self.assertEqual(app.screen_region({"x0": ["0.7"], "x1": ["0.2"], "y0": ["-3"], "y1": ["nope"]}), (0.2, 0.0, 0.7, 1.0))

    def test_taps_scrolls_keys_and_typing_happen_on_the_pc(self):
        port = self.serve()
        call = lambda query: json.loads(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            f"http://127.0.0.1:{port}/screen_input?{query}&token={self.TOKEN}", timeout=5).read())
        with mock.patch.object(app.Win32Input, "click") as click, mock.patch.object(app.Win32Input, "scroll") as scroll, \
                mock.patch.object(app.Win32Input, "press_key") as key, mock.patch.object(app.Win32Input, "type_unicode_text") as typed:
            self.assertEqual(call("do=click&x=0.25&y=0.5"), {"ok": True})
            self.assertEqual(call("do=right&x=1.5&y=-2"), {"ok": True})  # held to the screen's edges
            self.assertEqual(call("do=scroll&x=0.5&y=0.5&n=99"), {"ok": True})
            self.assertEqual(call("do=key&key=enter"), {"ok": True})
            self.assertEqual(call("do=key&key=f4"), {"ok": False})  # only the bar's keys
            self.assertEqual(call("do=type&text=" + urllib.parse.quote("héllo")), {"ok": True})
        click.assert_has_calls([mock.call(640, 800, button="left"), mock.call(2559, 0, button="right")])
        with mock.patch.object(app.Win32Input, "mouse_down") as down, mock.patch.object(app.Win32Input, "move_mouse") as move, \
                mock.patch.object(app.Win32Input, "mouse_up") as up:
            self.assertEqual(call("do=down&x=0.1&y=0.1"), {"ok": True})  # a drag: press, move, let go
            call("do=move&x=0.5&y=0.5")
            call("do=up&x=0.5&y=0.5")
        down.assert_called_once_with(256, 160)
        move.assert_called_once_with(1280, 800)
        up.assert_called_once_with(1280, 800)
        with mock.patch.object(app, "SCREEN_DRAG_IDLE", 0.05), mock.patch.object(app.Win32Input, "mouse_down"), \
                mock.patch.object(app.Win32Input, "held_buttons", {"left"}), mock.patch.object(app.Win32Input, "mouse_up") as up:
            call("do=down&x=0.1&y=0.1")  # and then the phone goes quiet
            deadline = time.monotonic() + 3
            while not up.called and time.monotonic() < deadline:
                time.sleep(0.02)
        up.assert_called_once_with(button="left")
        scroll.assert_called_once_with(20, 1280, 800)  # at most 20 notches at a time
        key.assert_called_once_with("enter")
        typed.assert_called_once_with("héllo")
        with self.assertRaises(HTTPError) as refused:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f"http://127.0.0.1:{port}/screen_input?do=click&x=0.5&y=0.5", timeout=5)
        self.assertEqual(refused.exception.code, 403)
        refused.exception.close()

    def test_the_pcs_sound_while_the_phone_listens(self):
        import http.client
        port = self.serve()

        class FakeSound:
            rate, closed = 24000, False

            def __init__(self):
                self.chunks = [b"\x01" * 80]

            def read(self):
                return self.chunks.pop() if self.chunks else b""

            def close(self):
                FakeSound.closed = True

        def listen():
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", f"/screen_sound?token={self.TOKEN}")
            return connection, connection.getresponse()

        with mock.patch.object(app.pc_sound, "Loopback", FakeSound), mock.patch.object(app, "SOUND_KEEPALIVE", 0.05):
            connection, reply = listen()
            self.assertEqual((reply.status, reply.getheader("X-Rate")), (200, "24000"))
            self.assertEqual(reply.read(80), b"\x01" * 80)
            self.assertEqual(reply.read(8), bytes(8))  # a moment of silence while nothing plays
            other, busy = listen()
            self.assertEqual(busy.status, 409)  # one phone at a time
            busy.read()
            other.close()
            reply.close()
            connection.close()  # the phone stopped listening
            deadline = time.monotonic() + 3
            while app.PC_SOUND._value < 1 and time.monotonic() < deadline:
                time.sleep(0.02)
        self.assertEqual(app.PC_SOUND._value, 1)
        self.assertTrue(FakeSound.closed)
        with mock.patch.object(app.pc_sound, "Loopback", side_effect=app.pc_sound.Unavailable("no speakers")):
            connection, reply = listen()
            self.assertEqual(reply.status, 503)
            reply.read()
            connection.close()
        self.assertEqual(app.PC_SOUND._value, 1)

    def test_a_full_size_screenshot_to_keep(self):
        from PIL import Image
        port = self.serve()
        with mock.patch.object(app.ImageGrab, "grab", return_value=Image.new("RGB", (2560, 1600), (10, 20, 30))):
            reply = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f"http://127.0.0.1:{port}/screenshot.png?token={self.TOKEN}", timeout=10)
            picture = Image.open(io.BytesIO(reply.read()))
        self.assertEqual((picture.format, picture.size, picture.getpixel((5, 5))), ("PNG", (2560, 1600), (10, 20, 30)))
        self.assertRegex(reply.headers["Content-Disposition"], r'^attachment; filename="PC screen \d{4}-\d\d-\d\d \d\d\.\d\d\.\d\d\.png"$')
        with self.assertRaises(HTTPError) as refused:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f"http://127.0.0.1:{port}/screenshot.png", timeout=5)
        self.assertEqual(refused.exception.code, 403)
        refused.exception.close()

    def test_a_picture_when_something_changed_else_nothing_new(self):
        import http.client
        port = self.serve()
        self.showing = b"\xff\xd8one\xff\xd9"

        class Screen:  # stands in for pc_capture.ScreenSource: it says when the screen changed
            latest, changed = object(), threading.Event()

            def wait_for_change(self, seconds):
                if self.changed.wait(seconds):
                    self.changed.clear()
                    return True
                return False

            def close(self):
                pass
        screen = Screen()

        def picture(region, width, quality, shot=None):
            self.asked = (region, width, quality)
            return self.showing, {"region": region, "screen": (2560, 1600)}

        def get(query):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.request("GET", f"/screen.jpg?{query}&token={self.TOKEN}")
            reply = connection.getresponse()
            answer = (reply.status, dict(reply.getheaders()), reply.read())
            connection.close()
            return answer

        with mock.patch.object(app, "screen_picture", side_effect=picture), mock.patch.object(app, "SCREEN_FEED", app.ScreenFeed(screen)), \
                mock.patch.object(app, "SCREEN_WAIT", 0.3), mock.patch.object(app.Win32Input, "get_cursor_pos", return_value=(640, 400)):
            status, headers, body = get("x0=0.25&y0=0.25&x1=0.75&y1=0.75&w=9999&q=sharp")
            self.assertEqual((status, body, headers["Content-Type"]), (200, b"\xff\xd8one\xff\xd9", "image/jpeg"))
            self.assertEqual((headers["X-Cursor"], headers["X-Screen"], headers["X-Region"]),
                             ("0.2500,0.2500", "2560,1600", "0.25000,0.25000,0.75000,0.75000"))
            self.assertEqual(self.asked, ((0.25, 0.25, 0.75, 0.75), 2560, 85))  # no wider than the sharp setting's widest
            started = time.monotonic()
            first = headers
            status, headers, body = get(f"since={first['X-Frame']}&cursor=0.2500,0.2500")
            self.assertEqual((status, body), (204, b""))  # unchanged for a moment: nothing new
            self.assertGreater(time.monotonic() - started, 0.25)
            self.showing = b"\xff\xd8two\xff\xd9"
            screen.changed.set()  # the screen changes
            started = time.monotonic()
            status, _, body = get(f"since={first['X-Frame']}&cursor=0.2500,0.2500")
            self.assertEqual((status, body), (200, b"\xff\xd8two\xff\xd9"))  # changed: at once
            self.assertLess(time.monotonic() - started, 0.25)
        self.assertEqual(self.asked[1:], (1280, 60))  # smooth, unless asked


class SoundTests(unittest.TestCase):
    # The buddy's engine sounds and the replies read aloud.
    def test_every_engine_sound_is_a_quiet_clean_wav(self):
        import engine_sounds
        import wave
        for name, (seconds, *_rest) in engine_sounds.SOUNDS.items():
            samples = engine_sounds.render(name)
            self.assertEqual(len(samples), int(seconds * engine_sounds.RATE), name)
            peak = max(abs(v) for v in samples)
            self.assertTrue(3000 < peak <= 0.43 * 32767, (name, peak))  # heard, but never loud
            self.assertLess(max(abs(v) for v in samples[:5] + samples[-5:]), 600, name)  # no click at either end
            self.assertEqual(samples, engine_sounds.render(name), name)  # the same every time
        with wave.open(io.BytesIO(engine_sounds.wav_bytes("blip"))) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, engine_sounds.RATE))

    def test_sounds_are_made_once_and_play_unless_turned_off(self):
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, {"APPDATA": temp_dir}), \
                mock.patch.object(app.winsound, "PlaySound") as play:
            sounds = app.EngineSounds()
            sounds.prepare()
            files = sorted(os.listdir(os.path.join(temp_dir, "Motorcycle Man", "sounds")))
            self.assertEqual(len(files), len(sounds.ready))
            with mock.patch("engine_sounds.wav_bytes") as made:
                app.EngineSounds().prepare()
            made.assert_not_called()  # already there
            self.assertTrue(sounds.play("wheelie"))
            self.assertFalse(sounds.play("blip"))  # too soon after the last one
            sounds.last -= 1
            app.save_setting("engine_sounds", False)
            self.assertFalse(sounds.play("blip"))
            self.assertFalse(sounds.play("no such sound"))
        play.assert_called_once()
        self.assertTrue(play.call_args.args[0].endswith(f"wheelie-{app.ENGINE_SOUND_VERSION}.wav"))

    def test_replies_are_read_aloud_without_links_or_symbols(self):
        self.assertEqual(app.spoken_words("**Done!** See https://example.com/a?b=1 🏍️ now"), "Done! See a link now")
        long = "The first sentence says enough. " + "More words that go on and on. " * 20
        spoken = app.spoken_words(long)
        self.assertTrue(spoken.startswith("The first sentence") and spoken.endswith("on and on.") and len(spoken) <= 240, spoken)
        self.assertLessEqual(len(app.spoken_words("word " * 200)), 243)
        self.assertEqual(app.spoken_words("🏍️ ✨"), "")
        voice = app.PcVoice()
        with mock.patch.object(app.PcVoice, "run"), mock.patch("threading.Thread.start"):
            self.assertFalse(voice.say("✨"))
            self.assertIsNone(voice.thread)  # nothing to say: no voice started
            voice.hush()
            self.assertTrue(voice.say("Opened Gmail."))
            voice.hush()
        self.assertEqual([voice.queue.get_nowait(), voice.queue.get_nowait()], ["Opened Gmail.", ""])

    def test_the_phone_answers_out_loud_what_was_said_out_loud(self):
        page = app.PHONE_PAGE
        for needed in ('id="prefTalkBack"', "function speakReply", "function hush", "replyAloud = true", "app.speak(words)"):
            self.assertIn(needed, page)


class AndroidAppTests(unittest.TestCase):
    # The Android app's share target, home-screen widget and quick-settings tile, and the page they drive.
    ANDROID = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Android")
    NS = "{http://schemas.android.com/apk/res/android}"

    def test_the_app_offers_sharing_a_widget_and_a_tile(self):
        manifest = ET.parse(os.path.join(self.ANDROID, "AndroidManifest.xml")).getroot()
        app_node = manifest.find("application")
        share = next(a for a in app_node.findall("activity") if a.get(self.NS + "name") == ".ShareActivity")
        actions = {f.find("action").get(self.NS + "name") for f in share.findall("intent-filter")}
        self.assertEqual(actions, {"android.intent.action.SEND", "android.intent.action.SEND_MULTIPLE"})
        widget = next(r for r in app_node.findall("receiver") if r.get(self.NS + "name") == ".TalkWidget")
        self.assertEqual(widget.find("meta-data").get(self.NS + "resource"), "@xml/widget_talk_info")
        tile = next(s for s in app_node.findall("service") if s.get(self.NS + "name") == ".TalkTile")
        self.assertEqual(tile.get(self.NS + "permission"), "android.permission.BIND_QUICK_SETTINGS_TILE")
        for part in ("res/layout/widget_talk.xml", "res/xml/widget_talk_info.xml", "res/drawable/ic_tile.xml",
                     "src/app/motorcycleman/remote/ShareActivity.java", "src/app/motorcycleman/remote/TalkTile.java"):
            self.assertTrue(os.path.exists(os.path.join(self.ANDROID, part)), part)
        self.assertGreaterEqual(int(manifest.get(self.NS + "versionCode")), 9)  # a new version, so phones update to it

    def test_the_page_does_what_the_widget_and_share_sheet_ask(self):
        for needed in ("window.appAction = what =>", "window.appAsk = text =>", "'listen'", "'screen'", "'remote'"):
            self.assertIn(needed, app.PHONE_PAGE)

    def test_a_watch_talks_to_the_pc_through_the_phone_s_notification(self):
        manifest = ET.parse(os.path.join(self.ANDROID, "AndroidManifest.xml")).getroot()
        self.assertIn("android.permission.POST_NOTIFICATIONS", [p.get(self.NS + "name") for p in manifest.findall("uses-permission")])
        receiver = next(r for r in manifest.find("application").findall("receiver") if r.get(self.NS + "name") == ".WatchNotice")
        self.assertEqual(receiver.get(self.NS + "exported"), "false")  # only its own notification's buttons reach it
        java = open(os.path.join(self.ANDROID, "src", "app", "motorcycleman", "remote", "WatchNotice.java"), encoding="utf-8").read()
        for needed in ("addRemoteInput(input)", "WearableExtender", '"/cmd"', '"/stop"', "goAsync()"):
            self.assertIn(needed, java)
        for needed in ('id="rowWatch"', "window.onWatch = on =>", "watchApp.watch(!watchApp.watchOn())"):
            self.assertIn(needed, app.PHONE_PAGE)

    def test_texts_to_the_phone_reach_the_pc_only_with_the_pin(self):
        manifest = ET.parse(os.path.join(self.ANDROID, "AndroidManifest.xml")).getroot()
        permissions = [p.get(self.NS + "name") for p in manifest.findall("uses-permission")]
        self.assertTrue({"android.permission.RECEIVE_SMS", "android.permission.SEND_SMS"} <= set(permissions))
        receiver = next(r for r in manifest.find("application").findall("receiver") if r.get(self.NS + "name") == ".SmsCommands")
        self.assertEqual(receiver.get(self.NS + "permission"), "android.permission.BROADCAST_SMS")  # only the system's texts
        java = open(os.path.join(self.ANDROID, "src", "app", "motorcycleman", "remote", "SmsCommands.java"), encoding="utf-8").read()
        for needed in ("isOn(context)", "pin(context)", "locked(context, sender)", "WRONG_LIMIT"):
            self.assertIn(needed, java)
        for needed in ('id="rowTexts"', "MotorcycleManAndroid.smsSettings()"):
            self.assertIn(needed, app.PHONE_PAGE)

    def test_a_phone_with_several_pcs_sees_which_one_this_is(self):
        self.assertEqual(app.phone_status()["pc"], app.socket.gethostname())
        for needed in ('id="rowPcs"', "MotorcycleManAndroid.choosePc()", "showPcName(state.pc)"):
            self.assertIn(needed, app.PHONE_PAGE)
        java = open(os.path.join(self.ANDROID, "src", "app", "motorcycleman", "remote", "MainActivity.java"), encoding="utf-8").read()
        self.assertIn("public void choosePc()", java)


class PhoneAppUpdateTests(unittest.TestCase):
    # The Motorcycle Man Android app updates itself from the PC, which hands out the app inside Motorcycle Man.exe.
    TOKEN = "app-update-test-token"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.apk = os.path.join(self.temp.name, "MotorcycleMan.apk")  # as in Motorcycle Man.exe, which unpacks to sys._MEIPASS
        write_apk(self.apk, binary_manifest(5, "1.4"))
        for patcher in (mock.patch.object(app, "PHONE_SESSION_TOKEN", self.TOKEN),
                        mock.patch.dict(app.PHONE_APP, {"key": None, "info": {}}),
                        mock.patch.dict(app.PHONE_CONTACT),
                        mock.patch.dict(os.environ, {"APPDATA": self.temp.name})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def as_built_exe(self):
        # Runs the code below as inside Motorcycle Man.exe, whose bundled files PyInstaller unpacks to sys._MEIPASS.
        frozen = mock.patch.object(sys, "frozen", True, create=True)
        unpacked = mock.patch.object(sys, "_MEIPASS", self.temp.name, create=True)
        frozen.start()
        unpacked.start()
        self.addCleanup(frozen.stop)
        self.addCleanup(unpacked.stop)

    def serve(self):
        # Motorcycle Man's phone server on a free port of this PC, as main() starts it.
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(FakeBridge(), 1000, 800))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    @staticmethod
    def get(url):
        # (status, headers, body), straight to Motorcycle Man (no proxy).
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=5) as reply:
                return reply.status, reply.headers, reply.read()
        except HTTPError as error:
            with error:
                return error.code, error.headers, error.read()

    def test_the_page_goes_on_a_home_screen_as_an_app(self):
        base = self.serve()
        self.assertEqual(self.get(base + "/manifest.webmanifest")[0], 403)  # it carries the link's secret
        status, headers, body = self.get(base + "/manifest.webmanifest?token=" + self.TOKEN)
        self.assertEqual(status, 200)
        self.assertIn("manifest+json", headers["Content-Type"])
        manifest = json.loads(body)
        self.assertEqual(manifest["start_url"], "/?token=" + self.TOKEN)  # it opens on the link
        self.assertEqual((manifest["name"], manifest["short_name"], manifest["display"]),
                         (f"Motorcycle Man on {app.socket.gethostname()}", "Motorcycle Man", "standalone"))  # which PC, for several
        for icon in manifest["icons"]:
            status, headers, body = self.get(base + icon["src"])  # the icons need no secret
            self.assertEqual((status, headers["Content-Type"], body[:8]), (200, "image/png", b"\x89PNG\r\n\x1a\n"))
        status, headers, body = self.get(base + "/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        worker = body.decode("utf-8")
        self.assertIn("addEventListener('fetch'", worker)
        self.assertIn("Can't reach your PC", worker)  # what it shows when the PC can't be reached
        page = app.ANDROID_WEB_CLIENT
        for part in ("beforeinstallprompt", "navigator.serviceWorker.register('/sw.js')", "apple-mobile-web-app-capable",
                     "'/manifest.webmanifest?token='", 'id="installTip"', 'id="installBtn"'):
            self.assertIn(part, page)

    def test_the_remote_buttons_need_the_link_and_answer_at_once(self):
        base = self.serve()
        self.assertEqual(self.get(base + "/remote?do=mute")[0], 403)
        with mock.patch.object(app, "remote_action", return_value={"say": "Volume 60%", "level": 60}) as action:
            status, headers, body = self.get(base + "/remote?do=volume_up&token=" + self.TOKEN)
        self.assertEqual((status, json.loads(body)), (200, {"say": "Volume 60%", "level": 60}))
        action.assert_called_once_with("volume_up")
        page = app.ANDROID_WEB_CLIENT
        for button in ("volume_down", "mute", "volume_up", "prev", "play_pause", "next", "brightness_down", "brightness_up",
                       "lock", "sleep"):
            self.assertIn(f"pressRemote('{button}', this)", page)
        self.assertIn("'Tap again'", page)  # sleep takes a second tap

    def test_the_version_is_read_from_the_app_itself(self):
        self.assertEqual(app.apk_version(self.apk), {"versionCode": 5, "versionName": "1.4"})
        utf8 = os.path.join(self.temp.name, "utf8.apk")
        write_apk(utf8, binary_manifest(12, "2.0 beta", utf8=True))
        self.assertEqual(app.apk_version(utf8), {"versionCode": 12, "versionName": "2.0 beta"})
        cut_short = os.path.join(self.temp.name, "cut.apk")
        write_apk(cut_short, binary_manifest(5, "1.4")[:300])
        not_an_app = os.path.join(self.temp.name, "notes.txt")
        with open(not_an_app, "w", encoding="utf-8") as f:
            f.write("not an app")
        for path in (cut_short, not_an_app, os.path.join(self.temp.name, "missing.apk")):
            with self.subTest(path=os.path.basename(path)):
                self.assertEqual(app.apk_version(path), {})

    @unittest.skipUnless(os.path.isfile(BUILT_APK), "Android\\MotorcycleMan.apk isn't built")
    def test_the_built_android_app_says_its_version(self):
        info = app.apk_version(BUILT_APK)
        self.assertGreater(info.get("versionCode", 0), 0)
        self.assertRegex(info.get("versionName", ""), r"^\d+(\.\d+)+$")

    def test_the_app_is_found_inside_the_exe_or_next_to_the_source(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(app.__file__)), "MotorcycleMan.spec"), encoding="utf-8") as f:
            spec = f.read()
        self.assertIn("os.path.join(SPECPATH, '..', 'Android', 'MotorcycleMan.apk')", spec)
        self.assertIn("datas.append((phone_app, '.'))", spec)  # unpacked next to Motorcycle Man's other files
        with mock.patch.object(app.os.path, "isfile", side_effect=lambda path: path == BUILT_APK):
            self.assertEqual(app.phone_app_path(), BUILT_APK)
        self.as_built_exe()
        self.assertEqual(app.phone_app_path(), self.apk)
        self.assertEqual(app.phone_app_info(), {"versionCode": 5, "versionName": "1.4", "size": os.path.getsize(self.apk)})
        os.remove(self.apk)
        self.assertIsNone(app.phone_app_path())
        self.assertEqual(app.phone_app_info(), {})

    def test_the_app_is_handed_out_only_with_the_links_secret(self):
        self.as_built_exe()
        base = self.serve()
        for path in ("/app.apk", "/app.apk?token=wrong-token", "/app.apk?token=", "/app.apk?x=" + self.TOKEN,
                     "/app_version", "/app_version?token=" + self.TOKEN[:-1]):
            with self.subTest(path=path):
                status, headers, body = self.get(base + path)
                self.assertEqual((status, headers["Content-Type"]), (403, "text/plain; charset=utf-8"))
                self.assertEqual(body, b"Forbidden: scan the current QR code from Motorcycle Man.")
        with open(self.apk, "rb") as f:
            apk = f.read()
        status, headers, body = self.get(base + "/app.apk?token=" + self.TOKEN)
        self.assertEqual((status, headers["Content-Type"], headers["Content-Length"]),
                         (200, "application/vnd.android.package-archive", str(len(apk))))
        self.assertEqual(body, apk)
        status, _headers, body = self.get(base + "/app_version?token=" + self.TOKEN)
        self.assertEqual((status, json.loads(body)), (200, {"versionCode": 5, "versionName": "1.4", "size": len(apk)}))
        status, _headers, body = self.get(base + "/status?token=" + self.TOKEN)
        self.assertEqual((status, json.loads(body)["app_version"]), (200, 5))

    def test_a_build_without_the_app_says_so(self):
        self.as_built_exe()
        os.remove(self.apk)
        base = self.serve()
        status, _headers, body = self.get(base + "/app_version?token=" + self.TOKEN)
        self.assertEqual((status, json.loads(body)), (200, {}))
        status, _headers, body = self.get(base + "/status?token=" + self.TOKEN)
        self.assertEqual(json.loads(body)["app_version"], 0)
        status, _headers, _body = self.get(base + "/app.apk?token=" + self.TOKEN)
        self.assertEqual(status, 404)

    def test_the_phone_page_tells_the_app_which_app_the_pc_has(self):
        page = app.ANDROID_WEB_CLIENT
        self.assertIn("tellAppAboutPcApp(state.app_version);", page)
        self.assertIn("window.MotorcycleManAndroid.pcHasApp(version);", page)
        self.assertIn("!window.MotorcycleManAndroid.pcHasApp) return;", page)  # older apps can't update themselves


if __name__ == "__main__":
    unittest.main(verbosity=2)
