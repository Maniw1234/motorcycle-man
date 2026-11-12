import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import array
import collections
import http.client
import http.server
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
import wave
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import ai_providers
import motorcycle_man as app
import speech_pack

QT = QApplication.instance() or QApplication([])

TOKEN = "talk-token"
KEY, OTHER_KEY = "a1b2c3d4-7", "0f0f0f0f-1"
QUIET, TALK = [40.0] * 10, [2500.0, 3000.0, 3500.0, 4000.0] * 8  # half a second of a quiet room; 1.6 s of talking


def chunk(level: float) -> bytes:
    # 50 ms of 16 kHz sound with about that loudness.
    level = int(level)
    return array.array("h", [level, -level] * 400).tobytes()


def wav_of(levels) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(chunk(level) for level in levels))
    return out.getvalue()


def seconds_of(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return w.getnframes() / w.getframerate()


def peak_of(wav_bytes: bytes) -> int:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        samples = array.array("h", w.readframes(w.getnframes()))
    return max(max(samples), -min(samples))


def fake_microphone(levels, error="", after_tap=False):
    # Stands in for MicRecorder._record (neither winmm nor a microphone is touched): the microphone gives these
    # readings (after a tap, once it is told to wait for the quiet), then a hold stays open until it is stopped.
    fed = threading.Event()

    def record(recorder, take):
        try:
            if error:
                take.error = error
                return
            if after_tap:
                take.until_quiet.wait(5)
            for level in levels:
                if take.stop.is_set() or recorder._over(take) or recorder._keep(take, chunk(level)):
                    return
            fed.set()
            take.stop.wait(5)
        finally:
            fed.set()
            take.done.set()

    record.fed = fed
    return record


class RecorderTests(unittest.TestCase):
    def test_a_long_hold_is_kept_whole(self):
        recorder, record = app.MicRecorder(), fake_microphone(QUIET + TALK * 12 + [40.0] * 6)  # 20 s
        with mock.patch.object(app.MicRecorder, "_record", record):
            session = recorder.start()
            self.assertTrue(record.fed.wait(5))
            wav_bytes = recorder.stop(session)
        self.assertAlmostEqual(seconds_of(wav_bytes), 20.0, places=2)  # the old limit cut it at 15 s
        self.assertTrue(recorder.heard_speech)
        self.assertEqual((app.MicRecorder.HOLD_SECONDS, app.MicRecorder.TAP_SECONDS, app.MicRecorder.BUFFERS), (120, 30, 40))

    def test_the_newest_start_wins_and_each_recording_is_handed_over_once(self):
        recorder, record = app.MicRecorder(), fake_microphone(QUIET + TALK)
        with mock.patch.object(app.MicRecorder, "_record", record):
            first = recorder.start()
            second = recorder.start()
            self.assertEqual(recorder.stop(first), b"")  # it can't take the newer recording
            self.assertTrue(recorder.replaced(first))
            self.assertFalse(recorder.replaced(second))
            self.assertTrue(recorder.stop(second).startswith(b"RIFF"))
            self.assertEqual(recorder.stop(second), b"")

    def test_after_a_tap_it_stops_when_the_talking_does(self):
        recorder, record = app.MicRecorder(), fake_microphone(QUIET + TALK + [40.0] * 40 + TALK, after_tap=True)
        with mock.patch.object(app.MicRecorder, "_record", record):
            session = recorder.start(app.MicRecorder.TAP_SECONDS)
            wav_bytes = recorder.stop(session, until_quiet=True)
        self.assertLess(seconds_of(wav_bytes), 4.0)  # ended in the quiet, before the later talking

    def test_ready_once_sound_arrives_and_a_muted_microphone_is_told_apart(self):
        recorder, take = app.MicRecorder(), app._Recording(1, 120)
        for _ in range(10):
            recorder._keep(take, chunk(0))  # the microphone's silence while it starts
        self.assertFalse(take.hearing.is_set())
        recorder._keep(take, chunk(40))
        self.assertTrue(take.hearing.is_set())
        recorder.levels = [0.0] * 30  # 1.5 s of nothing at all
        self.assertTrue(recorder.silent)
        recorder.levels = [0.0] * 29
        self.assertFalse(recorder.silent)
        recorder.levels = [0.0] * 30 + [40.0]
        self.assertFalse(recorder.silent)
        record = fake_microphone(QUIET + TALK)
        with mock.patch.object(app.MicRecorder, "_record", record):
            session = recorder.start()
            self.assertEqual(recorder.wait_until_hearing(session, 2.0), (True, ""))
            self.assertTrue(recorder.hearing.is_set())
            recorder.stop(session)

    def test_a_microphone_that_wont_open_says_why(self):
        recorder, said = app.MicRecorder(), "Windows couldn't open the microphone (error 4)."
        with mock.patch.object(app.MicRecorder, "_record", fake_microphone([], error=said)):
            session = recorder.start()
            self.assertEqual(recorder.wait_until_hearing(session, 2.0), (False, said))
            self.assertEqual(recorder.stop(session), b"")
        self.assertEqual(recorder.error, said)


class ListeningTests(unittest.TestCase):
    def test_longer_talking_gets_a_longer_pause(self):
        talk_4s, talk_2s = [2500.0] * 80, [2500.0] * 40
        self.assertEqual(app.quiet_after(app.speech_runs(QUIET + talk_4s)), 1.5)
        self.assertFalse(app.speech_ended(QUIET + talk_4s + [40.0] * 24))  # 1.2 s: still thinking
        self.assertTrue(app.speech_ended(QUIET + talk_4s + [40.0] * 32))  # 1.6 s: done
        self.assertFalse(app.speech_ended(QUIET + talk_2s + [40.0] * 21))  # 1.05 s
        self.assertTrue(app.speech_ended(QUIET + talk_2s + [40.0] * 22))  # 1.1 s after 2 s of talking
        self.assertTrue(app.speech_ended(QUIET + [2500.0] * 12 + [40.0] * 18))  # a short command: 0.9 s, as before

    def test_long_talking_isnt_taken_for_the_room(self):
        long_talk = [0.0] * 10 + [60.0] * 6 + [1800.0, 2200.0, 1500.0, 2600.0, 900.0, 2400.0] * 8 + [70.0] * 4
        self.assertTrue(app.speech_runs(long_talk))
        self.assertTrue(app.speech_runs([0.0] * 10 + [2500.0, 3000.0, 3500.0, 4000.0] * 6))  # talking from the start
        self.assertEqual(app.loud_moments([450.0] * 30), [])  # a steady, fairly loud room is still the room
        self.assertTrue(app.speech_runs([450.0] * 10 + [1500.0] * 6 + [450.0] * 10))  # and soft talking over it counts

    def test_a_tidy_recording_is_cut_to_the_talking_and_never_clips(self):
        levels = QUIET * 4 + [1000.0] * 20 + QUIET * 4  # 2 s quiet, 1 s of soft talking, 2 s quiet
        tidy = app.tidy_recording(wav_of(levels), levels)
        self.assertAlmostEqual(seconds_of(tidy), 1.6, places=2)  # with 0.3 s either side
        self.assertEqual(peak_of(tidy), 8000)  # at most 8 times louder
        levels = QUIET + [4000.0] * 20 + QUIET
        self.assertEqual(peak_of(app.tidy_recording(wav_of(levels), levels)), 16384)  # about -6 dBFS, no further
        levels = QUIET + [30000.0] * 20 + QUIET
        self.assertEqual(peak_of(app.tidy_recording(wav_of(levels), levels)), 30000)  # loud already: left alone
        self.assertEqual(app.tidy_recording(b"RIFF a recording", levels), b"RIFF a recording")
        quiet = wav_of(QUIET * 3)
        self.assertEqual(app.tidy_recording(quiet, QUIET * 3), quiet)  # nobody talked
        self.assertEqual(app.recording_levels(wav_of([40.0, 2500.0])), [40.0, 2500.0])


class PressRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.talk = {"key": "", "session": 0, "listening": False, "before": "", "ended": collections.deque(maxlen=32),
                     "started": collections.deque(maxlen=32), "dropped": collections.deque(maxlen=32),
                     "checked": collections.deque(maxlen=32), "timer": None}
        self.addCleanup(lambda: self.talk["timer"] and self.talk["timer"].cancel())
        for patcher in (mock.patch.dict(os.environ, {"APPDATA": self.temp.name}),
                        mock.patch.object(app, "PHONE_SESSION_TOKEN", TOKEN),
                        mock.patch.object(app, "MIC_RECORDER", app.MicRecorder()),
                        mock.patch.object(app, "PHONE_TALK", self.talk),
                        mock.patch.object(app, "PC_VOICE"),
                        mock.patch.object(app, "publish_task_status")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.microphone(QUIET + TALK + QUIET)
        self.run = self.patch(app, "execute_smart_action", return_value="Opened Notepad.")
        self.bridge = mock.Mock()
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(self.bridge, 2560, 1600))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def patch(self, *args, **kwargs):
        patcher = mock.patch.object(*args, **kwargs)
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def microphone(self, levels, **kwargs):
        patcher = mock.patch.object(app.MicRecorder, "_record", fake_microphone(levels, **kwargs))
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, path: str, token: str = TOKEN, **query):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        connection.request("GET", f"{path}?token={token}&{urllib.parse.urlencode(query)}")
        reply = connection.getresponse()
        data = reply.read()
        connection.close()
        try:
            return reply.status, json.loads(data)
        except ValueError:
            return reply.status, data

    def ready_published(self) -> bool:
        return mock.call(self.bridge, "Status: Ready!", running=False) in app.publish_task_status.call_args_list

    def test_a_hold_on_the_pcs_microphone(self):
        status, answer = self.get("/hold_start", s=KEY)
        self.assertEqual((status, answer), (200, {"ok": True, "ready": True, "error": ""}))
        app.PC_VOICE.hush.assert_called_once_with()
        app.publish_task_status.assert_called_with(self.bridge, "Status: Listening to your voice...", running=True)
        status, answer = self.get("/hold_stop", s=KEY, text="")
        self.assertEqual(answer, {"reply": "Opened Notepad."})
        self.run.assert_called_once()
        self.assertEqual(self.run.call_args.args[:4], ("", self.bridge, 2560, 1600))
        self.assertLess(seconds_of(self.run.call_args.kwargs["wav_bytes"]), 2.5)  # tidied to the talking
        self.assertEqual(self.get("/hold_stop", s=KEY)[1], {"reply": ""})  # one send per press
        self.run.assert_called_once()

    def test_the_phone_listening_itself_only_hushes_the_pc(self):
        self.patch(app.MicRecorder, "start", side_effect=AssertionError("the PC's microphone isn't used"))
        self.assertEqual(self.get("/hold_start", s=KEY, mic="phone")[1], {"ok": True, "ready": True, "error": ""})
        app.PC_VOICE.hush.assert_called_once_with()
        self.assertEqual(self.get("/hold_stop", s=KEY, text="open notepad")[1], {"reply": "Opened Notepad."})
        self.run.assert_called_once_with("open notepad", self.bridge, 2560, 1600)
        app.publish_task_status.assert_not_called()

    def test_a_start_after_its_stop_records_nothing(self):
        self.assertEqual(self.get("/hold_stop", s=KEY)[1], {"reply": app.PC_MIC_NOTHING})
        self.assertEqual(self.get("/hold_start", s=KEY)[1], {"ok": False, "late": True})
        self.assertEqual(app.MIC_RECORDER._sessions, 0)
        self.run.assert_not_called()

    def test_only_a_real_talk_key_and_the_links_secret(self):
        refused = {"ok": False, "error": app.TALK_KEY_PROBLEM, "reply": app.TALK_KEY_PROBLEM}
        self.assertEqual(self.get("/hold_start", s="../etc"), (400, refused))
        self.assertEqual(self.get("/hold_stop", s="A1B2C3D4-7", text="open notepad"), (400, refused))
        self.assertEqual(self.get("/listen_stop", s="a1b2c3d4-1234567890"), (400, refused))
        self.assertEqual(self.get("/hold_stop", token="wrong", s=KEY, text="open notepad")[0], 403)
        self.run.assert_not_called()
        self.assertEqual(app.MIC_RECORDER._sessions, 0)
        self.assertEqual(app.TALK_KEY_PROBLEM, "That didn't go through. Reload this page and try again.")

    def test_a_page_from_before_talk_keys_still_works(self):
        # Until it reloads (see pageChanged), a page loaded before an update sends no talk keys: its presses are
        # paired as before.
        self.assertEqual(self.get("/hold_start"), (200, {"ok": True, "ready": True, "error": ""}))
        self.assertTrue(self.talk["key"].startswith("legacy-"))
        self.assertEqual(self.get("/hold_stop", text=""), (200, {"reply": "Opened Notepad."}))
        self.assertLess(seconds_of(self.run.call_args.kwargs["wav_bytes"]), 2.5)
        self.assertEqual(self.get("/hold_start", mic="phone")[1], {"ok": True, "ready": True, "error": ""})
        self.assertEqual(self.get("/hold_stop", text="open notepad")[1], {"reply": "Opened Notepad."})
        self.run.assert_called_with("open notepad", self.bridge, 2560, 1600)
        self.assertEqual(self.run.call_count, 2)
        # Its tap: the stop waits for the quiet, and a second tap (no key) ends it.
        self.get("/hold_start")
        stopped = {}
        waiting = threading.Thread(target=lambda: stopped.update(answer=self.get("/hold_stop", text="", auto="1")))
        began = time.monotonic()
        waiting.start()
        self.until(lambda: self.talk["key"] in self.talk["ended"])
        self.assertEqual(self.get("/listen_stop")[0], 200)
        waiting.join(10)
        self.assertLess(time.monotonic() - began, 3.0)  # the microphone would have gone on for 5 s
        self.assertEqual(stopped["answer"], (200, {"reply": "Opened Notepad."}))

    def until(self, check, seconds=5.0):
        end = time.monotonic() + seconds
        while not check():
            self.assertLess(time.monotonic(), end, "never happened")
            time.sleep(0.01)

    def test_a_second_tap_ends_the_listening_after_its_stop_came(self):
        # V1-1: a tap's stop came first and waits for the quiet; the second tap (/listen_stop) finds the press by its
        # key alone and ends the listening at once, rather than at the microphone's limit.
        self.get("/hold_start", s=KEY)
        stopped = {}
        waiting = threading.Thread(target=lambda: stopped.update(answer=self.get("/hold_stop", s=KEY, auto="1")))
        began = time.monotonic()
        waiting.start()
        self.until(lambda: KEY in self.talk["ended"])
        self.assertEqual(self.get("/listen_stop", s=KEY)[0], 200)
        waiting.join(10)
        self.assertLess(time.monotonic() - began, 3.0)
        self.assertEqual(stopped["answer"], (200, {"reply": "Opened Notepad."}))
        self.run.assert_called_once()
        self.get("/listen_stop", s=OTHER_KEY)  # a key it doesn't know: nothing happens

    def test_a_tap_dropped_while_it_waits_for_the_quiet_runs_nothing(self):
        # V1-2: the page was hidden while the tap's stop waited for the quiet: the drop ends the listening, and
        # the waiting stop runs nothing.
        self.get("/hold_start", s=KEY)
        stopped = {}
        waiting = threading.Thread(target=lambda: stopped.update(answer=self.get("/hold_stop", s=KEY, auto="1")))
        began = time.monotonic()
        waiting.start()
        self.until(lambda: KEY in self.talk["ended"])
        self.assertEqual(self.get("/hold_stop", s=KEY, drop="1")[1], {"reply": ""})
        waiting.join(10)
        self.assertLess(time.monotonic() - began, 3.0)
        self.assertEqual(stopped["answer"], (200, {"reply": ""}))
        self.run.assert_not_called()
        self.assertIn(KEY, self.talk["dropped"])
        self.assertTrue(self.ready_published())

    def test_the_wake_words_and_shortcut_leave_a_press_alone(self):
        # V1-3: while a press on the phone records on the PC's microphone, "Hey Motorcycle Man" and Ctrl+Alt+M
        # don't take the microphone (which ended the press with nothing).
        self.patch(app, "ENGINE")
        self.patch(app, "set_mood")
        app.PC_VOICE.busy_until = 0.0  # Motorcycle Man isn't talking
        self.get("/hold_start", s=KEY)
        self.assertTrue(app.phone_press_on_pc_mic())
        self.assertFalse(app.wake_words_heard(self.bridge, 2560, 1600))
        app.listen_and_run(self.bridge, 2560, 1600)
        app.ENGINE.play.assert_not_called()
        self.assertEqual(app.MIC_RECORDER._sessions, 1)
        self.assertEqual(self.get("/hold_stop", s=KEY)[1], {"reply": "Opened Notepad."})
        self.assertFalse(app.phone_press_on_pc_mic())

    def test_presses_with_nothing_in_them_never_run_a_command(self):
        self.get("/hold_start", s=KEY)
        self.assertEqual(self.get("/hold_stop", s=KEY, drop="1")[1], {"reply": ""})  # the page was hidden
        self.assertTrue(self.ready_published())
        self.microphone(QUIET * 4)
        self.get("/hold_start", s=OTHER_KEY)
        self.assertEqual(self.get("/hold_stop", s=OTHER_KEY)[1], {"reply": app.PC_MIC_NOTHING})
        self.microphone([0.0] * 40)
        self.get("/hold_start", s="0f0f0f0f-2")
        self.assertEqual(self.get("/hold_stop", s="0f0f0f0f-2")[1], {"reply": app.PC_MIC_MUTED})
        self.run.assert_not_called()

    def test_a_microphone_that_failed_is_said_on_the_phone(self):
        said = "Windows couldn't open the microphone (error 4)."
        self.microphone([], error=said)
        self.assertEqual(self.get("/hold_start", s=KEY)[1], {"ok": False, "ready": False, "error": said})
        self.assertEqual(self.get("/hold_stop", s=KEY)[1], {"reply": said})
        self.run.assert_not_called()
        self.assertTrue(self.ready_published())

    def test_a_newer_press_takes_the_microphone(self):
        self.get("/hold_start", s=KEY)
        self.get("/hold_start", s=OTHER_KEY)  # another phone
        self.assertEqual(self.get("/hold_stop", s=KEY)[1], {"reply": "", "replaced": True})
        self.run.assert_not_called()
        self.assertEqual(self.get("/hold_stop", s=OTHER_KEY)[1], {"reply": "Opened Notepad."})

    def test_test_my_voice_writes_down_and_runs_nothing(self):
        with mock.patch.object(app, "hear", return_value=("open notepad", "windows")):
            self.get("/hold_start", s=KEY)
            status, answer = self.get("/hold_stop", s=KEY, check="1")
        self.assertEqual({k: answer[k] for k in ("reply", "heard", "engine")},
                         {"reply": "", "heard": "open notepad", "engine": "windows"})
        self.assertIsInstance(answer["ms"], int)
        with mock.patch.object(app, "hear", side_effect=app.TaskStopped()):  # a Stop for a task that is running
            self.get("/hold_start", s=OTHER_KEY)
            status, answer = self.get("/hold_stop", s=OTHER_KEY, check="1")
        self.assertEqual((status, answer["reply"], answer["heard"]), (200, "Try again in a moment.", ""))
        self.run.assert_not_called()
        self.assertTrue(self.ready_published())

    def test_send_it_runs_the_words_the_pc_wrote_down_once(self):
        # A press Android cut short, on the PC's microphone: the words come back unrun (check), then Send it sends
        # them back as text with the same key. They run once; a press whose words were dropped or empty runs nothing.
        with mock.patch.object(app, "hear", return_value=("open notepad", "windows")):
            self.get("/hold_start", s=KEY)
            self.assertEqual(self.get("/hold_stop", s=KEY, check="1")[1]["heard"], "open notepad")
        self.run.assert_not_called()
        self.assertEqual(self.get("/hold_stop", s=KEY, text="open notepad")[1], {"reply": "Opened Notepad."})
        self.run.assert_called_once_with("open notepad", self.bridge, 2560, 1600)
        self.assertEqual(self.get("/hold_stop", s=KEY, text="open notepad")[1], {"reply": ""})  # once only
        with mock.patch.object(app, "hear", return_value=("open mail", "windows")):
            self.get("/hold_start", s=OTHER_KEY)
            self.get("/hold_stop", s=OTHER_KEY, check="1")
        self.assertEqual(self.get("/hold_stop", s=OTHER_KEY, drop="1")[1], {"reply": ""})  # the page was left
        self.assertEqual(self.get("/hold_stop", s=OTHER_KEY, text="open mail")[1], {"reply": ""})
        with mock.patch.object(app, "hear", return_value=("", "windows")):
            self.get("/hold_start", s="0f0f0f0f-2")
            self.get("/hold_stop", s="0f0f0f0f-2", check="1")
        self.assertEqual(self.get("/hold_stop", s="0f0f0f0f-2", text="open mail")[1], {"reply": ""})
        self.run.assert_called_once()

    def test_the_presses_numbers_are_logged_without_words(self):
        talk = json.dumps({"e": "app" * 200, "r": 2, "ready": 410, "ms": 8200, "how": "seg\nopen my bank",
                           "words": "open my bank"})
        with self.assertLogs("motorcycle_man", "INFO") as logs:
            self.get("/hold_stop", s=KEY, text="open notepad", talk=talk)
        line = next(entry for entry in logs.output if "Phone talk:" in entry)
        self.assertNotIn("bank", line)
        self.assertNotIn("\n", line)
        self.assertLessEqual(len(line.split("Phone talk: ", 1)[1]), 300)
        self.assertIn('"ready":410', line)

    def test_the_names_the_phone_may_hear(self):
        self.assertIn("/words", app.PHONE_REQUESTS)
        app.ROUTINES.save("Morning", ["open mail"])
        app.add_to_history("open spotify", "Opened Spotify.")
        with mock.patch.dict(app._START_APPS, {"apps": {"spotify": "id1", "notepad": "id2"},
                                               "names": {"spotify": "Spotify", "notepad": "Notepad"}}):
            self.assertEqual(self.get("/words")[1], {"words": [], "ownMic": False})  # until the A/B check passes
            app.save_setting("phone_hints", True)
            status, answer = self.get("/words")
        self.assertEqual(answer, {"words": ["Motorcycle Man", "Morning", "Spotify"], "ownMic": False})
        self.assertEqual(self.get("/words", token="wrong")[0], 403)
        app.save_setting("phone_own_mic", True)
        self.assertIs(self.get("/words")[1]["ownMic"], True)
        with mock.patch.dict(app._START_APPS, {"apps": {f"app {i}": str(i) for i in range(80)}}):
            for first in range(0, 80, 10):
                app.add_to_history(" and ".join(f"open app {i}" for i in range(first, first + 10)), "Opened them.")
            self.assertEqual(len(app.hearing_words()), 50)
            self.assertLessEqual(len(app.hearing_prompt()), 400)


class HearingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.voice = wav_of(QUIET + TALK + QUIET)

    def gemini_answer(self, text):
        return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode())

    def test_sharp_ears_first_even_for_claude(self):
        app.save_setting("ai_provider", "claude")
        app.save_key(ai_providers.PROVIDERS["claude"]["key_name"], "sk-ant-test")
        with mock.patch.object(app.SPEECH_PACK, "ready", return_value=True), \
                mock.patch.object(app.SPEECH_PACK, "transcribe", return_value="open notepad") as pack, \
                mock.patch.object(app, "windows_dictation") as windows:
            self.assertEqual(app.hear(self.voice, "k"), ("open notepad", "pack"))
        windows.assert_not_called()
        self.assertTrue(pack.call_args.args[1].startswith("Motorcycle Man"))  # the names to expect
        self.assertGreater(pack.call_args.args[2], 1.0)  # how long someone talked
        self.assertIsInstance(pack.call_args.kwargs["job"], speech_pack.Job)

    def test_a_stop_stops_sharp_ears_too(self):
        # V1-9: a Stop while sharp ears write down a recording kills whisper-cli, not only the wait for it.
        with mock.patch.object(app.SPEECH_PACK, "ready", return_value=True), \
                mock.patch.object(app.SPEECH_PACK, "transcribe", side_effect=app.TaskStopped()) as pack, \
                mock.patch.object(app.SPEECH_PACK, "cancel") as cancel, \
                mock.patch.object(app, "windows_dictation") as windows, self.assertRaises(app.TaskStopped):
            app.hear(self.voice, "")
        cancel.assert_called_once_with(pack.call_args.kwargs["job"])
        windows.assert_not_called()

    def test_a_failing_pack_falls_to_gemini_with_the_names(self):
        app.save_setting("hearing_ai_fallback", True)  # "If sharp ears fail, use my AI"
        with mock.patch.object(app.SPEECH_PACK, "installed", return_value=True), \
                mock.patch.object(app.SPEECH_PACK, "ready", return_value=True), \
                mock.patch.object(app.SPEECH_PACK, "transcribe", side_effect=speech_pack.PackError("took too long")), \
                mock.patch.object(app.urllib.request, "urlopen", return_value=self.gemini_answer("Open Notepad.")) as urlopen:
            self.assertEqual(app.hear(self.voice, "key"), ("Open Notepad.", "gemini"))
        prompt = json.loads(urlopen.call_args.args[0].data)["contents"][0]["parts"][1]["text"]
        self.assertIn("Motorcycle Man", prompt)
        self.assertIn("best guess", prompt)
        self.assertIn("(none)", prompt)
        self.assertIn(app.TRANSCRIBE_MODELS[0], urlopen.call_args.args[0].full_url)

    def test_with_sharp_ears_the_voice_stays_on_this_pc(self):
        # V1-5: with sharp ears set up and the switch off (as it starts), a recording never goes to an AI, even when
        # sharp ears are sick: Windows writes it down, or nothing does.
        self.assertFalse(app.load_settings().get("hearing_ai_fallback", False))
        for provider in ("gemini", "openai"):
            app.save_setting("ai_provider", provider)
            app.save_key(ai_providers.PROVIDERS[provider]["key_name"], "test-key-for-" + provider)
            with mock.patch.object(app.SPEECH_PACK, "installed", return_value=True), \
                    mock.patch.object(app.SPEECH_PACK, "ready", return_value=False), \
                    mock.patch.object(ai_providers, "transcribe_openai", side_effect=AssertionError("sent to ChatGPT")), \
                    mock.patch.object(app.urllib.request, "urlopen", side_effect=AssertionError("sent away")), \
                    mock.patch.object(app, "quick_command", side_effect=AssertionError("nothing runs")), \
                    mock.patch.object(app, "try_instant_local_command", return_value=None), \
                    mock.patch.object(app, "installed_apps", return_value={}):
                with mock.patch.object(app, "windows_dictation", return_value="open notepad"):
                    self.assertEqual(app.hear(self.voice, "test-key-for-gemini"), ("open notepad", "windows"))
                with mock.patch.object(app, "windows_dictation", side_effect=RuntimeError("no dictation")):
                    said = app._execute_smart_action_locked("", mock.Mock(), 1000, 800, wav_bytes=self.voice)
                self.assertEqual(said, "I couldn't make out what you said. Try again, or type it.")
        # The switch on: sick sharp ears hand over to the user's AI.
        app.save_setting("ai_provider", "gemini")
        app.save_setting("hearing_ai_fallback", True)
        with mock.patch.object(app.SPEECH_PACK, "installed", return_value=True), \
                mock.patch.object(app.SPEECH_PACK, "ready", return_value=False), \
                mock.patch.object(app.urllib.request, "urlopen", return_value=self.gemini_answer("Open Notepad.")), \
                mock.patch.object(app, "windows_dictation", side_effect=AssertionError("Gemini answered")):
            self.assertEqual(app.hear(self.voice, "test-key-for-gemini"), ("Open Notepad.", "gemini"))

    def test_gemini_that_cant_answer_hands_over_to_windows(self):
        with mock.patch.object(app, "ask_gemini", side_effect=RuntimeError("over its free limit")), \
                mock.patch.object(app, "windows_dictation", return_value="open notepad") as windows:
            self.assertEqual(app.hear(self.voice, "key"), ("open notepad", "windows"))
        windows.assert_called_once_with(self.voice)
        with mock.patch.object(app, "ask_gemini", side_effect=RuntimeError("over its free limit")), \
                self.assertRaises(RuntimeError):
            app.hear(b"OggS", "key", "audio/ogg")  # Windows can't write down a voice message

    def test_voice_without_an_ai_key(self):
        with mock.patch.object(app, "windows_dictation", return_value="open the top email") as windows, \
                mock.patch.object(app.urllib.request, "urlopen", side_effect=AssertionError("no AI without a key")), \
                mock.patch.object(app, "try_instant_local_command", return_value=None), \
                mock.patch.object(app, "installed_apps", return_value={}), \
                mock.patch.object(app, "quick_command", return_value="Opened the top email.") as quick:
            said = app._execute_smart_action_locked("", mock.Mock(), 1000, 800, wav_bytes=self.voice)
        self.assertEqual(said, "Opened the top email.")
        windows.assert_called_once()
        self.assertEqual(quick.call_args.args[0], "open the top email")

    def test_windows_listens_in_us_english(self):
        self.assertIn("'en-US'", app.WINDOWS_DICTATION)
        self.assertIn("catch", app.WINDOWS_DICTATION)  # and in Windows' own language without it


class HearingRowTests(unittest.TestCase):
    # The SET UP card's Hearing row. The window is never on the real screen, and nothing is downloaded.
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = app.ControlCenterWindow("127.0.0.1", app.Bridge(), lambda text: None, token=TOKEN)
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.addCleanup(self.window.deleteLater)

    def row(self, state="", detail=""):
        self.window.show_hearing(state, detail)
        button = self.window.hearing_btn
        return self.window.hearing_lbl.text(), "" if button.isHidden() else button.text()

    def test_the_row_says_where_the_voice_goes(self):
        self.assertEqual(self.row(), ("Hearing: Windows · basic", ""))  # nothing pinned in this version: no button
        app.save_key(ai_providers.PROVIDERS["gemini"]["key_name"], "test-gemini-key")
        self.assertEqual(self.row(), ("Hearing: Gemini · your voice goes to Google", ""))
        with mock.patch.object(speech_pack, "available", return_value=True):
            self.assertEqual(self.row(), ("Hearing: Gemini · your voice goes to Google", "Get sharper ears"))
            with mock.patch.object(app.SPEECH_PACK, "installed", return_value=True):
                self.assertEqual(self.row(), ("Hearing: ✓ sharp ears · stays on this PC", "Change"))
                app.save_setting("hearing_ai_fallback", True)
                self.assertEqual(self.row(), ("Hearing: ✓ sharp ears, then Gemini", "Change"))
                app.save_setting("ai_provider", "claude")  # Claude can't listen: the voice stays here
                self.assertEqual(self.row(), ("Hearing: ✓ sharp ears · stays on this PC", "Change"))
                with mock.patch.object(app.SPEECH_PACK, "sick", return_value=True):
                    self.assertEqual(self.row(), ("Hearing: sharp ears had a problem", "Repair"))
            app.save_setting("ai_provider", "openai")
            app.save_key(ai_providers.PROVIDERS["openai"]["key_name"], "test-openai-key")
            self.assertFalse(app.ai_ready())  # no model picked yet, but hear() would still send it the voice
            self.assertEqual(self.row(), ("Hearing: ChatGPT · your voice goes to OpenAI", "Get sharper ears"))

    def test_getting_sharper_ears(self):
        with mock.patch.object(speech_pack, "available", return_value=True):
            with mock.patch.object(app.threading, "Thread") as thread:
                self.window.hearing_button()
            self.assertIs(thread.call_args.kwargs["target"], app.get_sharper_ears)
            thread.return_value.start.assert_called_once()
            self.assertEqual(self.row("progress", "42"), ("Downloading sharper ears… 42%", ""))
            self.assertEqual(self.row("checking"), ("Testing my new ears…", ""))
            self.assertEqual(self.row("failed", "the download didn't finish"),
                             ("Hearing: Windows · basic", "Get sharper ears"))
            self.assertEqual(self.window.status_lbl.text(),
                             "Couldn't get sharper ears: the download didn't finish. Nothing was changed.")
            with mock.patch.object(app.SPEECH_PACK, "installed", return_value=True):
                self.assertEqual(self.row("ready"), ("Hearing: ✓ sharp ears · stays on this PC", "Change"))
                self.assertEqual(self.window.status_lbl.text(), "Sharp ears ready! Your voice now stays on this PC.")
                # Change opens sharp ears' menu (a stand-in here: a real menu would wait on the screen).
                with mock.patch.object(self.window, "hearing_menu") as menu, \
                        mock.patch.object(app.threading, "Thread") as thread:
                    self.window.hearing_button()
                menu.return_value.exec.assert_called_once()
                thread.assert_not_called()

    def test_sharp_ears_menu(self):
        with mock.patch.object(app.SPEECH_PACK, "installed", return_value=True):
            menu = self.window.hearing_menu()
            self.addCleanup(menu.deleteLater)
            fallback, remove_action = menu.actions()
            self.assertEqual((fallback.text(), remove_action.text()), (app.HEARING_AI_FALLBACK, "Remove sharp ears"))
            self.assertEqual(app.HEARING_AI_FALLBACK, "If sharp ears fail, use my AI")
            self.assertTrue(fallback.isCheckable())
            self.assertFalse(fallback.isChecked())  # off until the user turns it on
            fallback.trigger()
            self.assertIs(app.load_settings()["hearing_ai_fallback"], True)
            self.assertEqual(self.window.status_lbl.text(), "Okay. If sharp ears fail, your AI writes it down.")
            again = self.window.hearing_menu()
            self.addCleanup(again.deleteLater)
            self.assertTrue(again.actions()[0].isChecked())
            again.actions()[0].trigger()
            self.assertIs(app.load_settings()["hearing_ai_fallback"], False)
            self.assertEqual(self.window.status_lbl.text(), "Okay. Your voice stays on this PC.")
            with mock.patch.object(app.SPEECH_PACK, "remove") as remove:
                remove_action.trigger()
            remove.assert_called_once_with()
            with mock.patch.object(app.SPEECH_PACK, "remove", side_effect=speech_pack.PackError("a file is in use")):
                remove_action.trigger()
            self.assertEqual(self.window.status_lbl.text(), "Couldn't remove sharp ears: a file is in use.")

    def test_the_download_reports_to_the_row(self):
        bridge = mock.Mock()

        def install(progress, checking):
            progress(30_000_000, 60_000_000)
            progress(30_000_001, 60_000_000)  # the same percent is shown once
            checking()
            return {"speed": 0.2}

        with mock.patch.object(app.SPEECH_PACK, "install", side_effect=install):
            app.get_sharper_ears(bridge)
        self.assertEqual([c.args for c in bridge.hearing_sig.emit.call_args_list],
                         [("progress", "50"), ("checking", ""), ("ready", "")])
        bridge.reset_mock()
        with mock.patch.object(app.SPEECH_PACK, "install", side_effect=speech_pack.PackError("the checksum didn't match")):
            app.get_sharper_ears(bridge)
        bridge.hearing_sig.emit.assert_called_once_with("failed", "the checksum didn't match")

    def test_the_note_for_ais_that_cant_listen_is_said_once(self):
        note = "Claude saved. Windows' own listening often mishears. Free sharp ears hear you much better."
        with mock.patch.object(speech_pack, "available", return_value=True):
            self.window.hearing_note("Gemini saved. ")
            self.assertNotIn("mishears", self.window.status_lbl.text())  # Gemini listens itself
            app.save_setting("ai_provider", "claude")
            self.window.hearing_note("Claude saved. ")
            self.assertEqual(self.window.status_lbl.text(), note)
            self.assertTrue(app.load_settings()["hearing_note_seen"])
            self.window.status_lbl.setText("Ready")
            self.window.hearing_note("Claude saved. ")
            self.assertEqual(self.window.status_lbl.text(), "Ready")


if __name__ == "__main__":
    unittest.main()
