import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import collections
import os
import tempfile
import threading
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import motorcycle_man as app
import speech_pack
from test_hold_to_talk import FLOWS_JS, NODE, run_node, talk_code
from test_hold_to_talk_pc import QUIET, TALK, fake_microphone, wav_of
from test_resume_ui import FakeJournal, entry
from test_task_resume import ResumeTestCase, plan

# Picking up a task that stopped halfway (task_journal) and talking to Motorcycle Man (hold to talk, hear()) in one
# app: words said while a task runs are written down once, beside the task, with or without an AI key; a press with
# nothing in it leaves the task alone; and the resume bar, the Paused chip and the window make room for each other.
# Everything is a stand-in: no microphone, screen, phone, browser, AI or internet.

QT = QApplication.instance() or QApplication([])
TOKEN = "resume-and-talk-token"
VOICE = wav_of(QUIET + TALK + QUIET)
REAL_TRANSCRIBE = app.transcribe_speech  # before ResumeTestCase puts a stand-in in its place


class VoiceBesideATaskTests(ResumeTestCase):
    # ResumeTestCase: the planner, the screen, the actions, quick_command, try_instant_local_command and
    # installed_apps are stand-ins, and transcribe_speech fails ("offline") unless a test says otherwise.
    def setUp(self):
        super().setUp()
        self.patch(mock.patch.object(app.SPEECH_PACK, "ready", return_value=False))  # no sharp ears here
        self.patch(mock.patch.object(app.urllib.request, "urlopen", side_effect=AssertionError("no internet")))
        self.patch(mock.patch.dict(app.STOP_WHY, {"why": ""}))
        app.TASK_STOP.clear()
        self.addCleanup(app.TASK_STOP.clear)

    def hold_the_task_lock(self):
        self.assertTrue(app.TASK_LOCK.acquire(blocking=False))  # a task is running
        self.addCleanup(app.TASK_LOCK.release)

    def test_keep_going_said_without_an_ai_key_while_a_task_runs(self):
        # No AI key: Windows writes the words down (as when no task runs), and "keep going" leaves the task alone.
        os.remove(os.path.join(self.temp.name, "Motorcycle Man", "keys.json"))
        self.assertFalse(app.ai_ready())
        self.patch(mock.patch.object(app, "transcribe_speech", REAL_TRANSCRIBE))
        windows = self.patch(mock.patch.object(app, "windows_dictation", return_value="keep going"))
        self.hold_the_task_lock()
        reply = app.execute_smart_action("", self.bridge, 1920, 1080, wav_bytes=VOICE)
        self.assertEqual(reply, "I'm still on it!")
        self.assertFalse(app.TASK_STOP.is_set())
        windows.assert_called_once_with(VOICE)
        self.gemini.assert_not_called()

    def test_without_an_ai_key_words_nobody_could_write_down_leave_the_task_alone(self):
        os.remove(os.path.join(self.temp.name, "Motorcycle Man", "keys.json"))
        self.patch(mock.patch.object(app, "transcribe_speech", REAL_TRANSCRIBE))
        self.patch(mock.patch.object(app, "windows_dictation", side_effect=RuntimeError("no dictation")))
        self.hold_the_task_lock()
        reply = app.execute_smart_action("", self.bridge, 1920, 1080, wav_bytes=VOICE)
        self.assertEqual(reply, "I couldn't make out what you said. Try again, or type it.")
        self.assertFalse(app.TASK_STOP.is_set())

    def test_a_stop_for_the_task_doesnt_cut_the_writing_down_short(self):
        # The task is stopped (the phone's Stop) while the words said beside it are written down: the writing down
        # finishes, and the words are handled. Inside a task the same stop does end a wait (see stoppable).
        self.patch(mock.patch.dict(app.TASK_TURNS, {"latest": 41, "running": 41}))
        self.hold_the_task_lock()
        turns_seen = []

        def dictation(wav_bytes):
            turns_seen.append(app.task_turn())
            app.stop_task()  # Stop, meanwhile
            threading.Event().wait(0.2)  # longer than stoppable's checks (time.sleep is a stand-in here)
            return "keep going"

        self.patch(mock.patch.object(app, "transcribe_speech", REAL_TRANSCRIBE))
        self.patch(mock.patch.object(app, "windows_dictation", side_effect=dictation))
        reply = app.execute_smart_action("", self.bridge, 1920, 1080, wav_bytes=VOICE)
        self.assertEqual(reply, "I'm still on it!")
        self.assertEqual(turns_seen, [0])  # beside the task, not in it
        self.assertEqual(app.task_turn(), 41)  # and this thread is back to seeing the task
        self.assertTrue(app.TASK_STOP.is_set())
        with self.assertRaises(app.TaskStopped):
            app.stoppable(lambda: "inside the task, a stop ends the wait")

    def test_each_recording_is_written_down_once(self):
        # Gemini's key, and nothing could write the words down: the running task is stopped, and Gemini listens to
        # the recording itself, without a second try at writing it down.
        started = threading.Event()

        def running_task():
            with app.TASK_LOCK:
                started.set()
                app.TASK_STOP.wait(5)  # a task ends when it is stopped

        worker = threading.Thread(target=running_task, daemon=True)
        worker.start()
        self.assertTrue(started.wait(5))
        self.gemini.side_effect = [plan([], message="Opened Notepad.", done=True, transcript="open notepad")]
        with mock.patch.object(app, "_execute_smart_action_locked", wraps=app._execute_smart_action_locked) as locked:
            reply = app.execute_smart_action("", self.bridge, 1920, 1080, wav_bytes=VOICE)
        worker.join(5)
        self.assertEqual(reply, "Opened Notepad.")
        self.assertEqual(app.transcribe_speech.call_count, 1)
        self.assertTrue(locked.call_args.args[5].get("unwritten"))
        parts = self.gemini.call_args.args[0]
        self.assertIn("audio/wav", [part.get("inline_data", {}).get("mime_type") for part in parts])

    def test_with_sharp_ears_and_the_switch_off_the_voice_never_goes_to_the_ai(self):
        # Sharp ears set up, "If sharp ears fail, use my AI" off: nothing could write it down, so the answer says
        # so, and the recording isn't handed to Gemini (the task isn't stopped for it either).
        self.patch(mock.patch.object(app.SPEECH_PACK, "installed", return_value=True))
        self.hold_the_task_lock()
        reply = app.execute_smart_action("", self.bridge, 1920, 1080, wav_bytes=VOICE)
        self.assertEqual(reply, "I couldn't make out what you said. Try again, or type it.")
        self.assertFalse(app.TASK_STOP.is_set())
        self.gemini.assert_not_called()


class PressBesideATaskTests(ResumeTestCase):
    # A press on the phone's helmet while a task runs, through hold_start_request and hold_stop_request with a
    # pretend microphone.
    def setUp(self):
        super().setUp()
        self.talk = {"key": "", "session": 0, "listening": False, "before": "", "ended": collections.deque(maxlen=32),
                     "started": collections.deque(maxlen=32), "dropped": collections.deque(maxlen=32),
                     "checked": collections.deque(maxlen=32), "timer": None}
        self.addCleanup(lambda: self.talk["timer"] and self.talk["timer"].cancel())
        self.patch(mock.patch.object(app, "MIC_RECORDER", app.MicRecorder()))
        self.patch(mock.patch.object(app, "PHONE_TALK", self.talk))
        self.patch(mock.patch.object(app, "PC_VOICE"))
        self.status = self.patch(mock.patch.object(app, "publish_task_status"))
        self.patch(mock.patch.dict(app.TASK_STATUS, {"status": "Status: Step 2 — typing the message...", "running": True}))
        self.patch(mock.patch.dict(app.STOP_WHY, {"why": ""}))
        app.TASK_STOP.clear()
        self.addCleanup(app.TASK_STOP.clear)
        self.assertTrue(app.TASK_LOCK.acquire(blocking=False))  # a task is running
        self.addCleanup(app.TASK_LOCK.release)

    def test_a_press_with_nothing_in_it_leaves_the_task_and_its_status(self):
        self.patch(mock.patch.object(app.MicRecorder, "_record", fake_microphone(QUIET * 4)))
        run = self.patch(mock.patch.object(app, "execute_smart_action", side_effect=AssertionError("nothing runs")))
        app.hold_start_request(self.bridge, "a1b2c3d4-7", False)
        self.assertEqual(app.hold_stop_request(self.bridge, 1920, 1080, "a1b2c3d4-7"), {"reply": app.PC_MIC_NOTHING})
        run.assert_not_called()
        self.assertFalse(app.TASK_STOP.is_set())
        self.assertEqual(self.status.call_args, mock.call(self.bridge, "Status: Step 2 — typing the message...", running=True))

    def test_keep_going_heard_by_the_phone_leaves_the_task_running(self):
        self.assertEqual(app.hold_start_request(self.bridge, "a1b2c3d4-8", True), {"ok": True, "ready": True, "error": ""})
        answer = app.hold_stop_request(self.bridge, 1920, 1080, "a1b2c3d4-8", spoken="keep going")
        self.assertEqual(answer, {"reply": "I'm still on it!"})
        self.assertFalse(app.TASK_STOP.is_set())
        self.assertEqual(app.hold_stop_request(self.bridge, 1920, 1080, "a1b2c3d4-8", spoken="keep going"),
                         {"reply": ""})  # one send per press


class WindowTests(unittest.TestCase):
    # The window with a paused task (its Resume task button in TRY A COMMAND) and the Hearing line (in SET UP's AI
    # row). Worked out off the screen, never shown.
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = FakeJournal(entry())
        for patcher in (mock.patch.dict(os.environ, {"APPDATA": self.temp.name}),
                        mock.patch.object(app, "TASKS", self.journal),
                        mock.patch.dict(app.PENDING_SEND, {"request": "", "time": 0.0, "task": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def window(self, telegram=None):
        window = app.ControlCenterWindow("127.0.0.1", app.Bridge(), lambda text: None, token=TOKEN, telegram=telegram,
                                         resume_cmd=lambda task_id: None)
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.addCleanup(window.deleteLater)
        return window

    def test_a_paused_task_and_the_hearing_line_keep_the_window_size(self):
        # V1-11: the Hearing line sits in the AI row, so the window keeps 708 (756 with Telegram).
        for telegram, height in ((None, 708), (mock.Mock(bot=None, state="", detail=""), 756)):
            window = self.window(telegram)
            self.assertEqual(window.pill.text(), "● Paused")
            self.assertFalse(window.resume_btn.isHidden())
            self.assertTrue(window.hearing_lbl.text().startswith("Hearing: "))
            central = window.centralWidget()
            central.layout().activate()
            self.assertEqual((window.width(), window.height()), (470, height))
            minimum = central.layout().minimumSize()
            self.assertLessEqual(minimum.height(), height)
            self.assertLessEqual(minimum.width(), 470)
            self.assertIs(window.hearing_lbl.parentWidget(), window.key_lbl.parentWidget())
            self.assertFalse(window.isVisible())

    def test_the_hearing_note_waits_while_a_task_is_paused(self):
        app.save_setting("ai_provider", "claude")
        with mock.patch.object(speech_pack, "available", return_value=True):
            window = self.window()
            line = window.status_lbl.text()
            self.assertEqual(line, "Paused at step 3: “send Dana the photos”  ·  You said stop.")
            window.hearing_note("Claude saved. ")
            self.assertEqual(window.status_lbl.text(), line)
            self.assertFalse(app.load_settings().get("hearing_note_seen", False))
            self.journal.entries.clear()  # picked up or forgotten
            window.refresh_resume()
            self.assertEqual(window.pill.text(), "● Ready")
            window.hearing_note()
            self.assertEqual(window.status_lbl.text(),
                             "Windows' own listening often mishears. Free sharp ears hear you much better.")
            self.assertTrue(app.load_settings()["hearing_note_seen"])


# The resume bar's code from the page, in a pretend page with a pretend clock: it waits while a press is on and
# while heard words wait on Send it, and doesn't touch the chip the press shows.
BAR_JS = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
let now = 10000;
const els = {};
function element(id) {
  const classes = new Set();
  return {id, textContent: '', dataset: {},
          classList: {add: (...c) => c.forEach(x => classes.add(x)), remove: (...c) => c.forEach(x => classes.delete(x)),
                      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), contains: c => classes.has(c)}};
}
const context = {console, Set, JSON, String, Promise, Date: {now: () => now},
                 document: {getElementById: id => (els[id] = els[id] || element(id))}, fetch: () => new Promise(() => {})};
vm.createContext(context);
vm.runInContext(`
  let busy = false, awaitingReply = false, holding = false, tapListening = false, offline = false, sleeping = false;
  let talkPhase = 'off', sendHeard = null, replyAloud = false, mode = 'idle', commandSeq = 0, stopRequested = false;
  const chipEl = document.getElementById('chip');
  const chips = [], said = [];
  function setChip(state, text) { chipEl.dataset.state = state; chips.push(text); }
  function say(t) { said.push(t); } function react() {} function wake() {} function setMode(m) { mode = m; }
  function showHint() {} function updateStop() {} function showReply() {}
  function requestUrl(path) { return path; }
`, context);
vm.runInContext(code, context);
const g = expr => vm.runInContext(expr, context);
const eq = (a, b, what) => { if (a !== b) throw new Error((what || '') + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b)); };
const bar = () => els.resumeBar.classList.contains('on');
const chip = () => els.chip.dataset.state;
const TASK = "({id: 'a1b2c3d4', state: 'paused', why: 'stop', words: 'You said stop.', step: 3, " +
             "request: 'send Dana the photos', ask: 'Resume task'})";
function poll(ms) {  // the /status checks, every 120 ms
  for (let t = 0; t < ms; t += 120) { now += 120; g('showPaused(' + TASK + ')'); }
}
poll(1700);
eq(bar(), true, 'shown once things are quiet');
eq(chip(), 'paused', 'the chip says Paused');
// A press: the page puts the bar away (startTalk) and shows its own chip; the bar and Paused wait for the press.
g("hidePaused(false); talkPhase = 'warming'; holding = true; setChip('listening', 'Getting ready')");
for (const phase of ['warming', 'held', 'quiet', 'ending']) {
  g("talkPhase = '" + phase + "'; holding = " + (phase === 'warming' || phase === 'held'));
  poll(2000);
  eq(bar(), false, phase + ': the bar waits');
  eq(chip(), 'listening', phase + ': the press keeps its chip');
}
// Android took the touch: the words wait on Send it, and so does the bar.
g("talkPhase = 'off'; holding = false; sendHeard = {words: 'open notepad'}; setChip('ready', 'Ready')");
poll(3000);
eq(bar(), false, 'not over Send it');
eq(chip(), 'ready', 'Send it keeps the chip');
// Quiet again: the bar comes back after its moment, not at once.
g('sendHeard = null');
poll(1200);
eq(bar(), false, 'not at once');
poll(600);
eq(bar(), true, 'back once things are quiet');
eq(chip(), 'paused', 'Paused again');
console.log('bar ok');
"""

# A new press puts the bar away at once, through the talk code itself (the talk flows' own pretend page).
assert FLOWS_JS.count("\n(async () => {\n") == 1
PRESS_JS = FLOWS_JS.split("\n(async () => {\n")[0] + r"""
(async () => {
  for (const engine of ['pc', 'app', 'browser']) {
    const P = page({engine, answers: {'/hold_start': READY}});
    P.g("pausedTask = {id: 'a1b2c3d4', state: 'paused'}");
    await P.down();
    eq(JSON.stringify(P.g('barHidden')), '[false]', engine + ': the press puts the bar away');
    eq(P.g('pausedTask'), null, engine + ': no task on show during the press');
    await P.advance(2000);
    await P.up();
    await P.advance(100);
    eq(P.g('barHidden').length, 1, engine + ': once a press');
  }
  console.log('press ok');
})().catch(err => { console.error(err.stack || String(err)); process.exit(1); });
"""


def resume_bar_code(page: str) -> str:
    at = page.index("// A task that stopped halfway waits on the PC")
    return page[at:page.index("// Talking: hold the face", at)]


@unittest.skipUnless(NODE, "Node isn't installed")
class PageTests(unittest.TestCase):
    def test_the_resume_bar_waits_for_a_press_and_for_send_it(self):
        ran = run_node(BAR_JS, resume_bar_code(app.ANDROID_WEB_CLIENT))
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("bar ok", ran.stdout)

    def test_a_press_puts_the_resume_bar_away(self):
        ran = run_node(PRESS_JS, talk_code(app.ANDROID_WEB_CLIENT))
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("press ok", ran.stdout)


class PageTextTests(unittest.TestCase):
    def test_one_page_with_both(self):
        page = app.ANDROID_WEB_CLIENT
        for piece in ('<div id="resumeBar"', '<button id="sendHeardBtn"', "showPaused(state.task || null);",
                      "pageChanged(state.page);", "const recognitionLang = listenLang();"):
            self.assertEqual(page.count(piece), 1, piece)
        self.assertNotIn("const recognitionLang = 'en-US';", page)
        for route in ("/words", "/hold_start", "/hold_stop", "/listen_stop"):
            self.assertIn(route, app.PHONE_REQUESTS)


if __name__ == "__main__":
    unittest.main()
