import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import ctypes
import io
import os
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

import motorcycle_man as app
import wake_word


def speak_to_file(text: str, path: str, voice: str = "Microsoft David Desktop") -> bool:
    # A recording of Windows' own voice saying text, for the recognizer to hear; False when Windows can't.
    script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.SelectVoice('{voice}'); $s.SetOutputToWaveFile('{path}'); $s.Speak('... {text} ...'); $s.Dispose()")
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                              timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and os.path.exists(path)


class FakeHelper:
    def __init__(self, lines, why=""):
        self.stdout, self.stderr = iter(lines), io.StringIO(why)
        self.returncode = 1

    def wait(self):
        return 1

    def poll(self):
        return 1

    def kill(self):
        pass


class WakeWordTests(unittest.TestCase):
    def test_lines_from_the_helper(self):
        self.assertEqual(wake_word.read_line("WAKE 0.95 hey motorcycle man\n"), (0.95, "hey motorcycle man"))
        for line in ("READY", "WAKE x hey", "something else", ""):
            self.assertIsNone(wake_word.read_line(line), line)

    def test_only_sure_hearings_wake_it_and_a_missing_microphone_is_told(self):
        heard, states = [], []
        lines = ["READY\n", "WAKE 0.95 hey motorcycle man\n", "WAKE 0.40 okay motorcycle man\n", "WAKE 0.81 okay motorcycle man\n"]
        listener = wake_word.WakeWord(heard=lambda: heard.append(1), changed=lambda *state: states.append(state))
        with mock.patch.object(wake_word, "start_helper", return_value=FakeHelper(lines, "No audio input is supplied")), \
                mock.patch.object(wake_word.WakeWord, "RESTARTS", 1):
            listener.run()
        self.assertEqual(len(heard), 2)
        self.assertEqual(states, [("on", ""), ("failed", "No microphone found.")])

    def test_it_stops_when_turned_off(self):
        stop = threading.Event()
        listener = wake_word.WakeWord()

        def lines():
            yield "READY\n"
            stop.wait(5)
            yield "WAKE 0.95 hey motorcycle man\n"

        listener.heard = mock.Mock()
        with mock.patch.object(wake_word, "start_helper", return_value=FakeHelper(lines())):
            listener.start()
            listener.stop()
            stop.set()
            listener.thread.join(5)
        self.assertFalse(listener.thread.is_alive())
        listener.heard.assert_not_called()
        self.assertEqual(listener.state, "off")

    def test_off_and_straight_back_on_keeps_listening(self):
        class BlockingHelper(FakeHelper):
            # Says READY, then waits like the real one until it is killed.
            def __init__(self):
                super().__init__([])
                self.killed = threading.Event()
                self.stdout = self.lines()

            def lines(self):
                yield "READY\n"
                self.killed.wait(5)

            def poll(self):
                return 1 if self.killed.is_set() else None

            def kill(self):
                self.killed.set()

        helpers = []
        listener = wake_word.WakeWord()
        with mock.patch.object(wake_word, "start_helper", side_effect=lambda *a: helpers.append(BlockingHelper()) or helpers[-1]):
            listener.start()
            deadline = time.monotonic() + 3
            while listener.state != "on" and time.monotonic() < deadline:
                time.sleep(0.01)
            listener.stop()
            listener.start()  # at once, while the first listener is still ending
            deadline = time.monotonic() + 3
            while len(helpers) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(helpers), 2)  # a new helper listens
            self.assertTrue(listener.thread.is_alive())
            listener.stop()
            listener.thread.join(3)

    def test_windows_hears_the_wake_words_and_not_other_talk(self):
        with tempfile.TemporaryDirectory() as folder:
            wake, other = os.path.join(folder, "wake.wav"), os.path.join(folder, "other.wav")
            if not (speak_to_file("hey motorcycle man", wake) and speak_to_file("I love my motorcycle, man", other)):
                self.skipTest("Windows' voice isn't available here")
            heard = wake_word.hear_file(wake)
            if not heard and not wake_word.hear_file(wake):
                self.skipTest("Windows' speech recognizer isn't available here")
            self.assertTrue(any(confidence >= wake_word.CONFIDENCE for confidence, _ in heard), heard)
            self.assertFalse([h for h in wake_word.hear_file(other) if h[0] >= wake_word.CONFIDENCE])


class HandsFreeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = mock.Mock()
        for patcher in (mock.patch.object(app, "MIC_RECORDER"), mock.patch.object(app, "PC_VOICE"),
                        mock.patch.object(app, "ENGINE"), mock.patch.object(app.time, "sleep"),
                        mock.patch.object(app, "load_settings", return_value={})):
            patcher.start()
            self.addCleanup(patcher.stop)
        app.MIC_RECORDER.stop.return_value = b"RIFF a recording"
        app.MIC_RECORDER.heard_speech = True
        app.MIC_RECORDER.error = ""

    def test_it_listens_does_it_and_answers_out_loud(self):
        with mock.patch.object(app, "execute_smart_action", return_value="Opened Gmail.") as run:
            app.listen_and_run(self.bridge, 1000, 800)
        app.ENGINE.play.assert_called_once_with("listen", always=True)
        app.MIC_RECORDER.stop.assert_called_once_with(until_quiet=True)
        run.assert_called_once_with("", self.bridge, 1000, 800, wav_bytes=b"RIFF a recording")
        app.PC_VOICE.say.assert_called_once_with("Opened Gmail.")
        self.assertFalse(app.PC_LISTENING.locked())

    def test_nobody_talked(self):
        app.MIC_RECORDER.heard_speech = False
        with mock.patch.object(app, "execute_smart_action") as run:
            app.listen_and_run(self.bridge, 1000, 800)
        run.assert_not_called()
        self.bridge.say_sig.emit.assert_called_with("I didn't hear anything.", 3000)

    def test_asking_again_while_it_listens_ends_the_listening(self):
        with app.PC_LISTENING:
            app.listen_and_run(self.bridge, 1000, 800)
        app.MIC_RECORDER.stop_listening.assert_called_once()
        app.MIC_RECORDER.start.assert_not_called()

    def test_its_own_voice_doesnt_wake_it(self):
        app.PC_VOICE.busy_until = app.time.monotonic() + 5
        self.assertFalse(app.wake_words_heard(self.bridge, 1000, 800))
        with app.PC_LISTENING:  # nor does it start again while it listens
            app.PC_VOICE.busy_until = 0.0
            self.assertFalse(app.wake_words_heard(self.bridge, 1000, 800))

    def test_the_shortcut(self):
        presses = []

        class User32:
            calls = 0

            def RegisterHotKey(self, window, key_id, modifiers, key):
                self.registered = (key_id, modifiers, key)
                return 1

            def GetMessageW(self, ref, *rest):
                User32.calls += 1
                if User32.calls > 1:
                    return 0
                ref._obj.message, ref._obj.wParam = app.WM_HOTKEY, app.HOTKEY_ID
                return 1

        user32 = User32()
        with mock.patch.object(app.ctypes, "WinDLL", return_value=user32), mock.patch.dict(app.HOTKEY, ready=False, event=threading.Event()):
            app.hotkey_loop(lambda: presses.append(1))
            self.assertTrue(app.HOTKEY["ready"])
        self.assertEqual(presses, [1])
        self.assertEqual(user32.registered, (app.HOTKEY_ID, app.MOD_CONTROL | app.MOD_ALT | app.MOD_NOREPEAT, app.VK_M))


if __name__ == "__main__":
    unittest.main()
