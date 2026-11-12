import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.server
import itertools
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

import motorcycle_man as app
import speech_pack
import task_journal
import test_speech_pack as pack_tests
from test_hold_to_talk import FLOWS_JS, NODE, run_node, talk_code
from test_resume_and_talk import resume_bar_code
from test_task_resume import SEND_BUTTON, ResumeTestCase, plan

# Guards found by a review of task resume and hold to talk: a "Yes, send it" on a routine's step, a failed
# update, what reaches tasks.json, an unnamed click after typing, a pick-up racing another command, the same
# command again, a refused forget; and on the phone, app 1.0's late words, app 1.1 ending a press the web view had
# lost, a refused press, leaving right after a let-go, and sharp ears tried beside a recording. Every one is a
# stand-in: no screen, microphone, phone, AI or internet.


class PickUpGuardTests(ResumeTestCase):
    def setUp(self):
        super().setUp()
        self.patch(mock.patch.object(app.urllib.request, "urlopen", side_effect=AssertionError("no internet")))
        self.patch(mock.patch.dict(app.STOP_WHY, {"why": ""}))
        self.patch(mock.patch.dict(app.LIVE_TASK, {"id": ""}))
        app.TASK_STOP.clear()
        self.addCleanup(app.TASK_STOP.clear)

    def held_routine_send(self, steps=("reply hi to Dana", "open the weather")):
        # A routine whose first step holds its send for a yes: the bar then says "Yes, send it".
        self.controls = SEND_BUTTON
        app.ROUTINES.save("Morning", list(steps))
        reply = self.run_command("morning", [plan([{"type": "type_text", "text": "hi"}, {"type": "click_element", "id": 2}])])
        self.assertEqual(reply, f"Morning stopped after 0 of {len(steps)}: Ready. Say yes to send it.")
        offered = app.resume_offer_for_ui()
        self.assertEqual((offered["why"], offered["ask"]), ("waiting_yes", "Yes, send it"))
        self.run_action.reset_mock()
        return offered

    def sends(self) -> int:
        return len([c for c in self.run_action.call_args_list if c.args[0].get("id") == 2])

    def busy_after_the_claim(self):
        # Another command (another phone, a schedule) takes the task lock just after the Resume tap claimed the task.
        claim = app.TASKS.claim

        def claim_then_busy(*args, **kwargs):
            got = claim(*args, **kwargs)
            app.TASK_LOCK.acquire()
            self.addCleanup(app.TASK_LOCK.release)
            return got
        return mock.patch.object(app.TASKS, "claim", side_effect=claim_then_busy)

    # resume-safety#0
    def test_yes_send_it_on_a_routine_step_sends_once_then_the_routine_goes_on(self):
        offered = self.held_routine_send()
        reply = self.resume([plan([{"type": "click_element", "id": 2}], "Sent!"),
                             plan([], "Here's the weather.", done=True)], task_id=offered["id"])
        self.assertEqual(reply, "Morning: all done.")
        self.assertEqual(self.sends(), 1)
        self.assertIn("The user has said yes: do the final send/post/run step now.", self.prompt(1))
        self.assertEqual(self.gemini.call_count, 3)  # the first go, the yes, and the routine's next step
        self.assertIsNone(app.resume_offer_for_ui())
        self.assertEqual(app.PENDING_SEND["request"], "")

    def test_yes_send_it_still_sends_when_the_routine_changed_since(self):
        offered = self.held_routine_send()
        app.ROUTINES.save("Morning", ["reply hi to Dana", "open the news"])
        reply = self.resume([plan([{"type": "click_element", "id": 2}], "Sent!")], task_id=offered["id"])
        self.assertEqual(reply, "Sent!")
        self.assertEqual(self.sends(), 1)
        self.assertIsNone(app.resume_offer_for_ui())

    # resume-safety#4
    def test_a_routine_pick_up_never_stops_a_command_that_just_began(self):
        def task(cmd, bridge, sw, sh, wav_bytes, heard, resume=None):
            if cmd == "post my status" and resume is None:
                heard["task"] = app.TASKS.start(cmd, "routine", app.ROUTINE_RUN.where)
                heard["ending"] = {"state": "paused", "why": "stop"}
                return "Okay, I stopped."
            return f"Did: {cmd}"
        locked = self.patch(mock.patch.object(app, "_execute_smart_action_locked", side_effect=task))
        app.ROUTINES.save("Morning", ["open gmail", "post my status", "open the weather"])
        app.execute_smart_action("morning", self.bridge, 1920, 1080)
        task_id = app.TASKS.offer()["id"]
        locked.reset_mock()
        app.TASK_STOP.clear()
        with self.busy_after_the_claim():
            reply = self.resume([], task_id=task_id)
        self.assertEqual(reply, "I'm still on it!")
        self.assertFalse(app.TASK_STOP.is_set(), "the other command was told to stop")
        locked.assert_not_called()  # neither the step nor the routine's next one ran
        entry = app.TASKS.get(task_id)
        self.assertEqual((entry["state"], entry["resumes"], entry["routine"]["at"]), ("paused", 0, 1))

    def test_a_yes_send_it_never_stops_a_command_that_just_began(self):
        self.controls = SEND_BUTTON
        self.run_command("reply hi to Dana", [plan([{"type": "type_text", "text": "hi"}, {"type": "click_element", "id": 2}])])
        task_id = app.resume_offer_for_ui()["id"]
        self.run_action.reset_mock()
        app.TASK_STOP.clear()
        with self.busy_after_the_claim():
            reply = self.resume([], task_id=task_id)
        self.assertEqual(reply, "I'm still on it!")
        self.assertFalse(app.TASK_STOP.is_set())
        self.assertEqual(self.sends(), 0)
        app.TASK_LOCK.release()
        self.addCleanup(app.TASK_LOCK.acquire)  # (the cleanup releases it again)
        self.assertEqual(app.resume_offer_for_ui()["ask"], "Yes, send it")  # still held, still offered

    # resume-safety#1
    def failed_update(self, stop):
        with mock.patch.dict(app.UPDATE, {"release": {"version": "1.1"}, "busy": False}), \
                mock.patch.object(app.app_update, "download_update", return_value="new.exe"), \
                mock.patch.object(app, "stop_task", side_effect=stop), \
                mock.patch.object(app, "STOP_WAIT_SECONDS", 0.05), \
                mock.patch.object(app.app_update, "start_install", side_effect=OSError("blocked by antivirus")):
            app.install_update(self.bridge)
        self.assertIn(("The update didn't work: blocked by antivirus", 8000), self.bridge.say_sig.values)
        self.assertEqual(self.bridge.update_quit_sig.values, [])

    def test_a_failed_update_never_says_i_updated_myself(self):
        task_id = app.LIVE_TASK["id"] = app.TASKS.start("send Dana the photos")

        def stop(why):
            app.STOP_WHY["why"] = why
            app.TASKS.pause(task_id, "paused", why)  # the task's thread ends and notes why
            return True
        self.failed_update(stop)
        offered = app.resume_offer_for_ui()
        self.assertEqual((offered["id"], offered["why"], offered["words"], offered["ask"]),
                         (task_id, "update_failed", "The update didn't work.", "Resume task"))
        self.assertFalse(app.TASK_LOCK.locked())

    def test_a_task_still_stopping_when_the_update_fails_notes_that(self):
        task_id = app.LIVE_TASK["id"] = app.TASKS.start("send Dana the photos")
        app.TASK_LOCK.acquire()  # the task hasn't ended yet
        try:
            self.failed_update(lambda why: app.STOP_WHY.update(why=why))
        finally:
            app.TASK_LOCK.release()
        self.assertEqual(app.STOP_WHY["why"], "update_failed")  # what the task notes when it ends
        self.assertEqual(app.TASKS.get(task_id)["state"], "running")  # not parked as cut off by an update

    # resume-safety#2
    def test_what_was_copied_or_typed_stays_out_of_the_note_on_disk(self):
        calls = itertools.count()
        app.clipboard_sequence.side_effect = lambda: min(next(calls), 1)  # the first action copies something
        app.clipboard_text.return_value = "Your code is 482913"
        steps = itertools.count()

        def planner(*args, **kwargs):
            n = next(steps)
            if n == 0:
                return plan([{"type": "hotkey", "keys": ["ctrl", "c"]}], todo="paste the code into the bank page")
            if n == 1:
                return plan([{"type": "type_text", "text": "Marigold Bakery"}],
                            todo="type 482913 into the code box, then sign as Marigold Bakery")
            self.assertIn("type 482913 into the code box", json.dumps(args[0]))  # the planner still knows it
            app.stop_task()
            app.stop_point()
        self.assertEqual(self.run_command("copy the code from my texts into the bank page", planner), "Okay, I stopped.")
        with open(os.path.join(self.temp.name, "Motorcycle Man", "tasks.json"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("482913", text)
        self.assertNotIn("Marigold", text)
        self.assertEqual(app.TASKS.offer()["todo"], "type (text) into the code box, then sign as (text)")

    # resume-safety#3
    def test_an_unnamed_click_right_after_typing_counts_as_maybe_sent(self):
        steps = itertools.count()

        def planner(*args, **kwargs):
            if next(steps) == 0:
                return plan([{"type": "type_text", "text": "hi"}, {"type": "click", "x": 600, "y": 500}],
                            todo="check it went")
            app.stop_task()
            app.stop_point()  # stopped between steps, after the x/y click on what may be Send
        self.assertEqual(self.run_command("send Dana a hi", planner), "Okay, I stopped.")
        entry = app.TASKS.offer()
        self.assertEqual((entry["unsure"], entry["done"]), ("", ["typing 2 characters", "clicking on the screen"]))
        self.assertEqual(entry["sent"], ["clicking on the screen right after typing (it may have sent it)"])
        self.run_action.reset_mock()
        reply = self.resume([plan([{"type": "type_text", "text": "hi", "press_enter": True}])], confirm=[False])
        self.assertIn(reply, ("Okay, I won't send it.", "Okay, I won't."))
        self.ask.assert_called_once()
        self.assertIn("it may have sent it", self.ask.call_args.args[1])
        self.assertEqual(self.run_action.call_args_list, [])  # nothing ran without a yes

    def test_a_click_with_nothing_typed_before_it_isnt_counted(self):
        steps = itertools.count()

        def planner(*args, **kwargs):
            if next(steps) == 0:
                return plan([{"type": "click", "x": 600, "y": 500}, {"type": "type_text", "text": "hi"}])
            app.stop_task()
            app.stop_point()
        self.run_command("write hi in the box", planner)
        self.assertEqual(app.TASKS.offer()["sent"], [])

    # resume-safety#5
    def test_the_same_command_done_again_leaves_no_stale_copy(self):
        self.stopped_mid_action()  # "send Dana a hi on Gmail", stopped mid Enter
        first = app.TASKS.offer()["id"]
        reply = self.run_command("Send Dana a hi  on Gmail", [plan([{"type": "press_key", "key": "tab"}],
                                                                  "Sent Dana a hi.", done=True)])
        self.assertEqual(reply, "Sent Dana a hi.")
        self.assertIsNone(app.TASKS.get(first))
        self.assertIsNone(app.resume_offer_for_ui())

    # resume-safety#6
    def test_the_phone_hears_when_a_forget_was_refused(self):
        token = "forget-test-token"
        self.patch(mock.patch.object(app, "PHONE_SESSION_TOKEN", token))
        self.patch(mock.patch.dict(app.PHONE_CONTACT))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(self.bridge, 1000, 800))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        url = f"http://127.0.0.1:{server.server_address[1]}/forget_task?token={token}&id="
        task_id = app.TASKS.start("open Gmail")
        app.TASKS.pause(task_id, "paused", "stop")
        app.TASKS.claim(task_id)  # being picked up just now
        self.assertEqual(json.loads(opener.open(url + task_id, timeout=5).read()),
                         {"ok": False, "reply": "It's already starting. Say stop to stop it."})
        app.TASKS.unclaim(task_id)
        self.assertEqual(json.loads(opener.open(url + task_id, timeout=5).read()), {"ok": True, "reply": "Okay, forgotten."})
        self.assertIsNone(app.TASKS.get(task_id))


class JournalGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.journal = task_journal.TaskJournal(lambda: os.path.join(self.temp.name, "tasks.json"))

    # resume-safety#2
    def test_scrub_keeps_typed_and_copied_words_out(self):
        scrub = task_journal.scrub
        self.assertEqual(scrub("type 482913 into the code box and press Enter", ["Your code is 482913"], "copy the code"),
                         "type (text) into the code box and press Enter")
        self.assertEqual(scrub("type HELLO world, then save", ["hello world"]), "type (text), then save")
        self.assertEqual(scrub("send Dana the note", ["hi Dana"], "send Dana a hi"), "send Dana the note")
        self.assertEqual(scrub("type the address in the box", ["12 Secret Lane, Leeds"]), "type the address in the box")
        self.assertEqual(scrub("check 12 Secret Lane", ["12 Secret Lane, Leeds"]), "check (text) (text) (text)")
        self.assertEqual(scrub("press Enter to send", ["", None]), "press Enter to send")
        self.assertEqual(scrub("", ["x"]), "")

    # resume-safety#5
    def test_a_new_go_at_the_same_request_replaces_its_stopped_copy(self):
        old = self.journal.start("Send Dana the photos")
        self.journal.pause(old, "paused", "stop")
        other = self.journal.start("open the weather")
        self.journal.pause(other, "paused", "stop")
        new = self.journal.start("send  dana the photos")
        self.assertIsNone(self.journal.get(old))
        self.assertEqual(self.journal.get(other)["state"], "paused")
        self.assertEqual(self.journal.get(new)["state"], "running")
        # A go at it being picked up isn't touched, and a pick-up keeps its own entry.
        self.journal.pause(new, "paused", "stop")
        self.journal.claim(other)
        again = self.journal.start("open the weather")
        self.assertEqual(self.journal.get(other)["state"], "resuming")
        self.journal.pause(again, "paused", "stop")
        self.assertEqual(self.journal.start("", resume_id=other), other)
        self.assertIsNotNone(self.journal.get(new))

    # resume-safety#1
    def test_a_failed_update_has_words_of_its_own(self):
        task_id = self.journal.start("send Dana the photos")
        self.journal.pause(task_id, "paused", "update")
        self.assertTrue(self.journal.relabel(task_id, "update_failed"))
        shown = task_journal.for_ui(self.journal.get(task_id))
        self.assertEqual((shown["why"], shown["words"]), ("update_failed", "The update didn't work."))
        self.assertIn("an update was tried and didn't work", task_journal.resume_context(self.journal.get(task_id))["note"])


# ptt-compat#5
class PackCheckBesideRecordingTests(unittest.TestCase):
    def setUp(self):
        self.case = pack_tests.SpeechPackTests("test_not_offered_until_pinned")
        self.case.setUp()  # its stand-ins: the downloads in memory, Windows' voice and whisper-cli faked
        self.addCleanup(self.case.doCleanups)

    def whisper_runs(self) -> int:
        return len([c for c in self.case.run.calls if c[0][0].endswith("whisper-cli.exe")])

    def test_trying_new_ears_waits_for_a_recording_being_written_down(self):
        pack = self.case.make()
        pack.install()
        job = speech_pack.Job()
        thread, ended = self.case.writing_down(pack, job)
        runs, checking, out = self.whisper_runs(), threading.Event(), {}

        def repair():
            try:
                out["info"] = pack.install(checking=checking.set)
            except speech_pack.PackError as exc:
                out["error"] = exc
        repairing = threading.Thread(target=repair, daemon=True)
        repairing.start()
        self.assertTrue(checking.wait(5))
        time.sleep(0.3)
        self.assertEqual(self.whisper_runs(), runs, "the new ears were tried beside the recording")
        pack.cancel(job)  # the task's Stop: the recording's whisper-cli is the one stopped
        thread.join(5)
        self.assertEqual(len(self.case.run.kills), 1)
        self.assertIsInstance(ended.get("error"), speech_pack.PackCancelled)
        repairing.join(5)
        self.assertIn("info", out, out)
        self.assertEqual(self.whisper_runs(), runs + 1)
        self.assertIsNone(pack.process)

    def test_a_check_that_cant_wait_says_so_and_changes_nothing(self):
        pack = self.case.make()
        info = pack.install()
        thread, ended = self.case.writing_down(pack)
        with mock.patch.object(speech_pack, "BUSY_WAIT_SECONDS", 0.1):
            with self.assertRaises(speech_pack.PackError) as said:
                pack.install()
        self.assertEqual(str(said.exception), "sharp ears were busy writing down what you said, so try again in a moment")
        self.case.run.hold.set()
        thread.join(5)
        self.assertEqual(ended, {"words": "Hey Motorcycle Man, open Notepad."})
        with open(os.path.join(pack.folder(), "pack.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), info)  # the pack that was there stays


# Phone page flows, through the page's own talk code.
GUARDS_JS = FLOWS_JS.split("(async () => {")[0] + r"""
const texts = P => P.sends().map(u => P.param(u, 'text'));
const listens = P => P.calls.filter(c => c.startsWith('app.listen ')).length;
const starts = P => P.calls.filter(c => c.startsWith('app.startListening')).length;
(async () => {
  // ptt-compat#0: app 1.0, a quick new press while the last press's final words are still coming. 1.0 names no
  // press, so its late words must not count for the new one (they went with the last press).
  {
    const P = page({engine: 'app1'});
    await P.down(1);
    P.g(`window.onAndroidSpeech('send the email', false)`);
    await P.advance(1500);
    await P.up(1);
    eq(P.g('talkPhase'), 'ending', 'A: waiting for the final words');
    await P.advance(300);
    await P.down(2);
    eq(JSON.stringify(texts(P)), '["send the email"]', 'A: the last press words went with the new press');
    P.g(`window.onAndroidSpeech('send the email', true)`);  // press 1's final, delivered after press 2 began
    await P.advance(5);
    P.g(`window.onAndroidSpeech('to bob', false)`);
    await P.advance(1500);
    await P.up(2);
    P.g(`window.onAndroidSpeech('to bob', true)`);
    await P.settle();
    eq(JSON.stringify(texts(P)), '["send the email","to bob"]', 'A: each press once');
    eq(JSON.stringify(P.sends().map(u => P.param(u, 's'))), '["abcd0123-1","abcd0123-2"]', 'A: keys');
  }
  {
    const P = page({engine: 'app1'});
    await P.down(1);
    await P.advance(1500);
    await P.up(1);
    await P.advance(200);
    await P.down(2);
    const before = starts(P);
    P.g(`window.onAndroidSpeechError('')`);  // the last press's own end, after stopping
    await P.advance(1000);
    eq(starts(P), before, "O: the last press's late end starts nothing");
    eq(P.g('talkPhase'), 'held', 'O: still held');
    P.g(`window.onAndroidSpeechError('')`);  // later, a piece of this press ends: it listens again
    await P.advance(1000);
    ok(starts(P) > before, 'O: listens again after its own piece');
    const first = page({engine: 'app1'});
    await first.down(1);
    first.g(`window.onAndroidSpeech('open notepad', false)`);  // a first press hears at once
    eq(first.g('live'), 'open notepad', 'O: a first press is not held back');
  }
  // ptt-compat#1: app 1.1, the web view lost the touch while the finger stayed down, then the app ended the press
  // by itself (another app took the microphone): no finger news will come.
  {
    const P = page({engine: 'app', fingerStillDown: true});
    await P.down(1);
    const key = P.key();
    P.g(`window.onAndroidSpeechState('ready', '${key}', 'seg')`);
    P.g(`window.onAndroidSpeech('turn on the lights', false, '${key}')`);
    await P.advance(2000);
    P.fire('window:pointercancel', P.event('pointercancel', P.els.stage, 1));
    eq(P.g('talkPhase'), 'held', 'B: the app still follows the finger');
    P.g(`window.onAndroidSpeechError('Another app is using the microphone. Close it and try again.', '${key}')`);
    P.g(`window.onAndroidSpeech('turn on the lights', true, '${key}')`);
    await P.settle();
    eq(P.g('talkPhase'), 'off', 'B: the press is over');
    eq(P.g('talkPointer'), null, 'B: no finger kept');
    ok(P.els.sendHeardBtn.classList.contains('on'), 'B: the words wait on Send it');
    eq(P.sends().length, 0, 'B: nothing sent by itself');
    await P.down(3);
    ok(P.key() !== key, 'B: a new tap is a new press');
    eq(listens(P), 2, 'B: and listens');
  }
  {
    const P = page({engine: 'app', fingerStillDown: true});
    await P.down(1);
    const key = P.key();
    P.g(`window.onAndroidSpeechError('Allow the microphone for Motorcycle Man, then try again.', '${key}')`);
    P.g(`window.onAndroidSpeech('', true, '${key}')`);
    await P.advance(900);
    P.fire('window:pointercancel', P.event('pointercancel', P.els.stage, 1));
    await P.advance(2000);
    eq(P.g('talkPhase'), 'off', 'K: a refused press is over once the touch is gone');
    eq(P.lastSaid(), 'Allow the microphone for Motorcycle Man, then try again.', 'K: says why');
    await P.down(2);
    eq(listens(P), 2, 'K: the next tap listens');
  }
  // ptt-compat#2: a press the app refuses at once keeps saying why while the finger stays down.
  for (const problem of ['Allow the microphone for Motorcycle Man, then try again.',
                         'This phone has no speech recognition service.']) {
    const P = page({engine: 'app'});
    await P.down(1);
    const key = P.key();
    P.g(`window.onAndroidSpeechError(${JSON.stringify(problem)}, '${key}')`);
    P.g(`window.onAndroidSpeech('', true, '${key}')`);
    const n = P.said().length;
    await P.advance(7000);
    eq(JSON.stringify(P.said().slice(n)), '[]', 'P: nothing over the reason: ' + problem);
    eq(P.lastSaid(), problem, 'P: the reason shows');
    await P.up(1);
    await P.advance(1000);
    eq(P.g('talkPhase'), 'off', 'P: over');
    eq(P.lastSaid(), problem, 'P: and still says why');
  }
  // ptt-compat#3: leaving the app right after letting go of a hold sends what was heard; mid-hold, nothing.
  for (const engine of ['app', 'app1', 'browser']) {
    const P = page({engine});
    await P.down(1);
    const key = P.key();
    if (engine === 'app') { P.g(`window.onAndroidSpeechState('ready', '${key}', 'seg')`); P.g(`window.onAndroidSpeech('lock the pc', false, '${key}')`); }
    if (engine === 'app1') P.g(`window.onAndroidSpeech('lock the pc', false)`);
    if (engine === 'browser') { P.sr().onaudiostart(); P.sr().onresult({results: [[{transcript: 'lock the pc'}]]}); }
    await P.advance(1500);
    await P.up(1);
    eq(P.g('talkPhase'), 'ending', 'N ' + engine + ': let go');
    await P.hide();
    await P.advance(5000);
    eq(P.g('talkPhase'), 'off', 'N ' + engine + ': over');
    eq(JSON.stringify(texts(P)), '["lock the pc"]', 'N ' + engine + ': the words went once');
    ok(!P.said().includes('I stopped listening when you left. Hold me again.'), 'N ' + engine + ': not dropped');
  }
  {
    const P = page({engine: 'app'});
    await P.down(1);
    const key = P.key();
    P.g(`window.onAndroidSpeechState('ready', '${key}', 'seg')`);
    P.g(`window.onAndroidSpeech('lock the pc', false, '${key}')`);
    await P.advance(1500);
    await P.up(1);
    P.g(`window.onAndroidSpeechState('stopped', '${key}')`);  // the app went to the background
    await P.settle();
    eq(JSON.stringify(texts(P)), '["lock the pc"]', 'N stopped: the words went once');
    const Q = page({engine: 'app'});
    await Q.down(1);
    const qkey = Q.key();
    Q.g(`window.onAndroidSpeechState('ready', '${qkey}', 'seg')`);
    Q.g(`window.onAndroidSpeech('delete the file', false, '${qkey}')`);
    await Q.advance(1500);
    await Q.hide();  // still held: dropped, as before
    await Q.advance(5000);
    eq(Q.sends().length, 0, 'N held: nothing sent');
    eq(Q.lastSaid(), 'I stopped listening when you left. Hold me again.', 'N held: says so');
  }
  console.log('guards ok');
})().catch(err => { console.error(err.stack || String(err)); process.exit(1); });
"""

# resume-safety#6 on the phone: a forget the PC refused brings the task's bar back.
FORGET_JS = r"""
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(process.argv[2], 'utf8');
async function run(answer) {
  let now = 10000;
  const els = {};
  function element(id) {
    const classes = new Set();
    return {id, textContent: '', dataset: {},
            classList: {add: (...c) => c.forEach(x => classes.add(x)), remove: (...c) => c.forEach(x => classes.delete(x)),
                        toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), contains: c => classes.has(c)}};
  }
  const reply = {ok: true, json: () => Promise.resolve(answer)};
  const context = {console, Set, JSON, String, Promise, Date: {now: () => now},
                   document: {getElementById: id => (els[id] = els[id] || element(id))},
                   fetch: () => Promise.resolve(reply)};
  vm.createContext(context);
  vm.runInContext(`
    let busy = false, awaitingReply = false, holding = false, tapListening = false, offline = false, sleeping = false;
    let talkPhase = 'off', sendHeard = null, replyAloud = false, mode = 'idle', commandSeq = 0, stopRequested = false;
    const chipEl = document.getElementById('chip');
    const said = [];
    function setChip(state) { chipEl.dataset.state = state; }
    function say(t) { said.push(t); } function react() {} function wake() {} function setMode(m) { mode = m; }
    function showHint() {} function updateStop() {} function showReply() {}
    function requestUrl(path) { return path; }
  `, context);
  vm.runInContext(code, context);
  const g = expr => vm.runInContext(expr, context);
  const TASK = "({id: 'a1b2c3d4', state: 'paused', why: 'stop', words: 'You said stop.', step: 3, " +
               "request: 'send Dana the photos', ask: 'Resume task'})";
  const poll = ms => { for (let t = 0; t < ms; t += 120) { now += 120; g('showPaused(' + TASK + ')'); } };
  poll(1700);
  if (!els.resumeBar.classList.contains('on')) throw new Error('bar not shown');
  g('forgetPaused()');
  await new Promise(r => setTimeout(r, 20));
  poll(5000);  // the PC still offers it on /status
  return {bar: els.resumeBar.classList.contains('on'), said: g('said')};
}
(async () => {
  const refused = await run({ok: false, reply: "It's already starting. Say stop to stop it."});
  if (!refused.bar) throw new Error('a refused forget hid the task for good');
  if (!refused.said.includes("It's already starting. Say stop to stop it.")) throw new Error(JSON.stringify(refused.said));
  const done = await run({ok: true, reply: 'Okay, forgotten.'});
  if (done.bar) throw new Error('a forgotten task came back');
  console.log('forget ok');
})().catch(err => { console.error(err.stack || String(err)); process.exit(1); });
"""


@unittest.skipUnless(NODE, "Node isn't installed")
class PageGuardTests(unittest.TestCase):
    def test_talk_guards(self):
        ran = run_node(GUARDS_JS, talk_code(app.ANDROID_WEB_CLIENT))
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("guards ok", ran.stdout)

    def test_a_refused_forget_brings_the_task_back(self):
        ran = run_node(FORGET_JS, resume_bar_code(app.ANDROID_WEB_CLIENT))
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("forget ok", ran.stdout)


if __name__ == "__main__":
    unittest.main()
