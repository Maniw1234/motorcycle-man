import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import task_journal
from task_journal import TaskJournal

NOW = 1_800_000_000.0  # a fixed wall clock; each test moves it on by hand


def tasks_path() -> str:
    # Like motorcycle_man's lambda: os.path.join(app_data_dir(), "tasks.json"), APPDATA read on every call.
    folder = os.path.join(os.environ["APPDATA"], "Motorcycle Man")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "tasks.json")


class JournalTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.dict(os.environ, {"APPDATA": self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = NOW
        self.journal = self.fresh_journal()

    def fresh_journal(self) -> TaskJournal:
        # A new journal on the same file: what the app sees after a restart.
        return TaskJournal(tasks_path, clock=lambda: self.now)

    def on_disk(self) -> dict:
        with open(tasks_path(), encoding="utf-8") as f:
            return json.load(f)

    def paused_task(self, request="send Dana the photos", why="stop", state="paused", **fields) -> str:
        task_id = self.journal.start(request, "phone")
        if fields:
            self.assertTrue(self.journal.checkpoint(task_id, **fields))
        self.journal.pause(task_id, state, why)
        return task_id


class JournalFileTests(JournalTestCase):
    def test_each_step_is_saved_whole_with_no_part_file_left(self):
        task_id = self.journal.start("open Gmail and send Dana the photos", "voice")
        self.assertTrue(self.journal.mark_in_flight(task_id, {"type": "open_app", "app": "Gmail"}, step=1))
        self.assertTrue(self.journal.mark_done_action(task_id, "Opened Gmail"))
        self.assertTrue(self.journal.checkpoint(task_id, step=1, todo="write to Dana, attach the photos",
                                                history=[{"step": 1, "result": "Opened Gmail"}]))
        saved = self.on_disk()
        self.assertEqual(saved["v"], 1)
        [entry] = saved["tasks"]
        self.assertEqual((entry["id"], entry["state"], entry["step"], entry["done"], entry["in_flight"]),
                         (task_id, "running", 1, ["Opened Gmail"], ""))
        self.assertEqual(entry["todo"], "write to Dana, attach the photos")
        self.assertEqual(os.listdir(os.path.dirname(tasks_path())), ["tasks.json"])

    def test_copied_text_never_reaches_the_disk(self):
        task_id = self.journal.start("copy the address and paste it in Notes")
        history = [{"action": {"type": "hotkey", "keys": ["ctrl", "c"]},
                    "result": 'Pressed ctrl+c (copied 22 characters: "12 Secret Lane, Leeds")'}]
        self.assertTrue(self.journal.checkpoint(task_id, copied="12 Secret Lane, Leeds", history=history))
        with open(tasks_path(), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("Secret Lane", text)
        self.assertIn("(copied 22 characters)", text)
        self.assertEqual(self.journal.get(task_id)["copied"], "12 Secret Lane, Leeds")  # this run still knows it
        self.assertEqual(self.fresh_journal().get(task_id)["copied"], "")  # after a restart it's gone

    def test_a_new_appdata_folder_is_read_afresh(self):
        first = self.paused_task()
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        with mock.patch.dict(os.environ, {"APPDATA": other.name}):
            self.assertIsNone(self.journal.offer())  # the next test run's folder holds nothing yet
            second = self.paused_task("open the weather")
            self.assertEqual(self.journal.offer()["id"], second)
        self.assertEqual(self.journal.offer()["id"], first)

    def test_a_broken_or_unknown_file_counts_as_empty(self):
        good = {"id": "a1b2c3d4", "request": "open the weather", "state": "paused", "why": "off", "paused_at": NOW}
        for content in ("{broken", "[]", json.dumps({"v": 2, "tasks": [good]}), json.dumps({"v": 1, "tasks": "x"}),
                        json.dumps({"v": 1, "tasks": [{"id": 5, "state": "paused"}, {"id": "b", "state": "lost"},
                                                      {"id": "c", "state": "paused", "step": float("inf")}, "x"]})):
            with open(tasks_path(), "w", encoding="utf-8") as f:
                f.write(content)
            self.assertIsNone(self.fresh_journal().offer(), content)
        with open(tasks_path(), "w", encoding="utf-8") as f:
            json.dump({"v": 1, "tasks": [dict(good, done="not a list", step="3", resumes=99, fails=99, sent="x")]}, f)
        entry = self.fresh_journal().offer()
        self.assertEqual((entry["id"], entry["done"], entry["step"], entry["resumes"], entry["fails"], entry["sent"]),
                         ("a1b2c3d4", [], 0, 99, 3, []))

    def test_a_file_that_cannot_be_written_never_stops_the_task(self):
        os.makedirs(tasks_path())  # a folder where tasks.json should be: every save fails
        with self.assertLogs("motorcycle_man", "WARNING") as logs:
            task_id = self.journal.start("open the weather")
            self.assertFalse(self.journal.checkpoint(task_id, step=2))
            self.assertFalse(self.journal.mark_done_action(task_id, "Opened the weather"))
        self.assertEqual(len(logs.output), 1)  # said once, not at every step
        self.assertIn("Could not save the task journal", logs.output[0])
        self.assertEqual(self.journal.get(task_id)["step"], 2)  # this run can still pick it up
        self.assertFalse(os.path.exists(tasks_path() + ".part"))
        os.rmdir(tasks_path())
        self.assertTrue(self.journal.checkpoint(task_id, step=3))  # the next save puts it all on disk
        self.assertEqual(self.on_disk()["tasks"][0]["done"], ["Opened the weather"])

    def test_long_values_are_cut_to_size(self):
        task_id = self.journal.start("x" * 900)
        history = [{"step": n, "result": "r" * 900} for n in range(15)]
        history.append({"action": {"type": "write_file", "path": "C:\\notes.txt", "content": "c" * 5000}, "result": "ok"})
        self.journal.checkpoint(task_id, done=[f"action {n}" for n in range(80)], todo="t" * 900, history=history,
                                in_flight="i" * 900)
        entry = self.on_disk()["tasks"][0]
        self.assertEqual(len(entry["request"]), 500)
        self.assertEqual((len(entry["done"]), entry["done"][0], entry["done"][-1]), (50, "action 30", "action 79"))
        self.assertEqual(len(entry["todo"]), 200)
        self.assertEqual(len(entry["in_flight"]), 160)
        self.assertEqual(len(entry["history"]), 10)
        self.assertEqual(entry["history"][0], {"step": 6, "result": "r" * 120})
        self.assertEqual(entry["history"][-1]["action"]["content"], "<5000 chars>")  # its length only
        self.assertEqual(entry["history"][-1]["action"]["path"], "C:\\notes.txt")

    def test_what_was_typed_is_kept_as_its_length_only(self):
        task_id = self.journal.start("fill in the code from my texts")
        typed = {"type": "type_text", "text": "482913", "press_enter": True}
        history = [{"step": 1, "action": typed, "result": 'Typed "482913"'},
                   {"step": 1, "action": {"type": "search_web", "query": "what is 482913"},
                    "result": "Searched web for what is 482913"},
                   {"step": 2, "action": {"type": "write_file", "path": "C:\\code.txt", "content": "482913 " * 40},
                    "result": "Wrote file C:\\code.txt"},
                   {"step": 2, "action": {"type": "type_text", "text": "e"}, "result": 'Typed "e"'}]
        self.assertTrue(self.journal.checkpoint(task_id, step=2, history=history))
        self.assertTrue(self.journal.mark_in_flight(task_id, typed, step=2))
        with open(tasks_path(), encoding="utf-8") as f:
            self.assertNotIn("482913", f.read())
        saved = self.on_disk()["tasks"][0]
        self.assertEqual(saved["history"][0], {"step": 1, "action": {"type": "type_text", "text": "<6 chars>",
                                                                     "press_enter": True},
                                               "result": 'Typed "<6 chars>"'})
        self.assertEqual(saved["history"][1]["result"], "Searched web for <14 chars>")
        self.assertEqual((saved["history"][2]["action"]["content"], saved["history"][2]["result"]),
                         ("<280 chars>", "Wrote file C:\\code.txt"))
        self.assertEqual(saved["history"][3]["result"], 'Typed "<1 char>"')  # not "Typ<1 char>d"
        self.assertEqual(saved["in_flight"], "typing 6 characters and pressing Enter")
        self.assertEqual(history[0]["action"]["text"], "482913")  # the task's own copy is left alone

    def test_what_cant_be_undone_is_kept_apart(self):
        task_id = self.journal.start("send Dana the photos")
        self.assertTrue(self.journal.mark_done_action(task_id, "opening Gmail"))
        self.assertTrue(self.journal.mark_done_action(task_id, "pressing Enter", sent=["pressing Enter"]))
        self.journal.pause(task_id, "paused", "stop")
        entry = self.fresh_journal().get(task_id)
        self.assertEqual((entry["done"], entry["sent"]), (["opening Gmail", "pressing Enter"], ["pressing Enter"]))
        seed = task_journal.resume_context(entry)
        self.assertEqual(seed["sent"], ["pressing Enter"])
        self.assertTrue(seed["note"].endswith(" Already sent or not undoable: pressing Enter. Do NOT do these again "
                                              "unless the screen shows they did not happen."), seed["note"])

    def test_a_routine_step_keeps_its_routine(self):
        routine = {"id": "r1", "name": "Morning", "steps": ["open gmail", "open the weather"], "at": 1, "made": 5}
        task_id = self.journal.start("open the weather", "routine", routine=routine)
        self.journal.pause(task_id, "paused", "waiting_yes")
        self.assertEqual(self.fresh_journal().get(task_id)["routine"],
                         {"id": "r1", "name": "Morning", "steps": ["open gmail", "open the weather"], "at": 1})
        self.assertIsNone(self.journal.get(self.journal.start("x", routine={"name": "no id"}))["routine"])

    def test_a_restart_reads_the_task_back(self):
        task_id = self.paused_task(step=3, done=["Opened Gmail", "Clicked Compose"], todo="attach the photos")
        entry = self.fresh_journal().offer()
        self.assertEqual((entry["id"], entry["state"], entry["why"], entry["step"]), (task_id, "paused", "stop", 3))
        self.assertEqual(entry["done"], ["Opened Gmail", "Clicked Compose"])


class JournalStateTests(JournalTestCase):
    def test_checkpoints_only_land_while_the_task_runs(self):
        task_id = self.journal.start("send Dana the photos")
        self.journal.pause(task_id, "paused", "stop")
        self.assertFalse(self.journal.checkpoint(task_id, step=9))
        self.assertFalse(self.journal.mark_in_flight(task_id, "pressing Enter"))
        self.assertFalse(self.journal.mark_done_action(task_id, "Pressed Enter"))
        self.assertEqual(self.journal.get(task_id)["step"], 0)
        running = self.journal.start("open the weather")
        self.journal.recover("update")  # the update parks it while its thread still runs
        self.assertFalse(self.journal.checkpoint(running, step=4))
        self.assertEqual(self.journal.get(running)["state"], "paused")
        self.assertTrue(self.journal.forget(running))
        self.assertFalse(self.journal.checkpoint(running, step=5))
        self.assertIsNone(self.journal.get(running))  # a late write never brings it back

    def test_a_pause_after_recover_keeps_the_recover_reason(self):
        task_id = self.journal.start("send Dana the photos")
        self.journal.mark_in_flight(task_id, {"type": "press_key", "key": "enter"}, step=3)
        [recovered] = self.journal.recover("update")
        self.assertEqual((recovered["why"], recovered["unsure"], recovered["in_flight"]), ("update", "pressing Enter", ""))
        self.now += 1
        entry = self.journal.pause(task_id, "paused", "stop")  # the stopped thread's own pause comes later
        self.assertEqual((entry["why"], entry["paused_at"], entry["unsure"]), ("update", NOW, "pressing Enter"))

    def test_claim_works_once_and_unclaim_puts_it_back(self):
        task_id = self.paused_task(why="unchanged", state="stuck")
        entry = self.journal.claim(task_id)
        self.assertEqual((entry["state"], entry["resumes"]), ("resuming", 1))
        self.assertIsNone(self.journal.claim(task_id))  # a second tap
        self.assertIsNone(self.journal.offer())  # not offered while it is being resumed
        self.assertTrue(self.journal.unclaim(task_id))
        entry = self.journal.get(task_id)
        self.assertEqual((entry["state"], entry["resumes"], entry["why"]), ("stuck", 0, "unchanged"))
        self.assertNotIn("was", entry)
        self.assertFalse(self.journal.unclaim(task_id))
        self.assertEqual(self.journal.claim()["id"], task_id)  # no id: the one on offer

    def test_a_resume_keeps_the_id_and_what_was_done(self):
        task_id = self.paused_task(step=3, done=["Opened Gmail"], todo="attach the photos")
        self.journal.claim(task_id)
        self.assertEqual(self.journal.start("ignored", resume_id=task_id), task_id)
        entry = self.journal.get(task_id)
        self.assertEqual((entry["state"], entry["why"], entry["paused_at"], entry["request"], entry["resumes"]),
                         ("running", "", None, "send Dana the photos", 1))
        self.assertEqual((entry["step"], entry["done"], entry["todo"]), (3, ["Opened Gmail"], "attach the photos"))
        self.assertTrue(self.journal.checkpoint(task_id, step=4))
        self.assertIsNone(self.journal.finish(task_id))
        self.assertIsNone(self.journal.get(task_id))
        self.assertEqual(self.on_disk()["tasks"], [])

    def test_recover_moves_the_cut_off_action_to_unsure(self):
        parked = self.paused_task("open the weather", why="limit")
        resuming = self.paused_task("open Notepad", why="failed", state="stuck")
        self.journal.claim(resuming)
        running = self.journal.start("send Dana the photos")
        self.journal.mark_in_flight(running, {"type": "type_text", "text": "Here you go", "press_enter": True}, step=2)
        changed = self.journal.recover("crash")
        self.assertEqual(sorted(t["id"] for t in changed), sorted([running, resuming]))
        entry = self.journal.get(running)
        self.assertEqual((entry["state"], entry["why"], entry["in_flight"], entry["unsure"], entry["offered"]),
                         ("paused", "crash", "", "typing 11 characters and pressing Enter", False))
        self.assertEqual(self.journal.get(resuming)["resumes"], 1)  # a crash mid-resume still counts the try
        self.assertEqual(self.journal.get(parked)["why"], "limit")
        self.assertEqual(self.journal.recover("crash"), [])

    def test_recover_after_restart_names_the_reason(self):
        for argv, why in ((["mm.exe", "--tray", "--updated"], "update"), (["mm.exe", "--tray", "--restarted"], "crash"),
                          (["mm.exe"], "off")):
            task_id = self.journal.start("open the weather")
            [entry] = self.fresh_journal().recover_after_restart(argv)
            self.assertEqual((entry["id"], entry["why"]), (task_id, why), argv)
            self.journal = self.fresh_journal()
            self.journal.forget(task_id)

    def test_parked_tasks_expire_by_reason(self):
        for why, seconds in (("stop", 600), ("newer", 1800), ("limit", 3 * 3600), ("crash", 3 * 3600)):
            self.now = NOW
            task_id = self.paused_task(why=why)
            self.now = NOW + seconds - 1
            self.assertEqual(self.journal.offer()["id"], task_id, why)
            self.now = NOW + seconds
            self.assertIsNone(self.journal.offer(), why)
            self.assertIsNone(self.journal.claim(task_id), why)
            self.assertEqual(self.journal.expire(), 1, why)
            self.assertEqual(self.on_disk()["tasks"], [], why)

    def test_expired_tasks_are_dropped_when_the_file_is_read(self):
        self.paused_task(why="stop")
        self.now += 601
        self.assertIsNone(self.fresh_journal().offer())
        self.assertEqual(self.on_disk()["tasks"], [])

    def test_only_the_newest_three_are_kept(self):
        ids = []
        for n in range(5):
            self.now = NOW + n
            ids.append(self.paused_task(f"task {n}", why="newer"))
        running = self.journal.start("task 5")
        parked = [t for t in self.on_disk()["tasks"] if t["state"] in task_journal.PARKED]
        self.assertEqual([t["id"] for t in sorted(parked, key=lambda t: t["paused_at"], reverse=True)], ids[:1:-1])
        self.assertEqual({t["id"] for t in self.on_disk()["tasks"]}, set(ids[2:]) | {running})

    def test_three_resumes_at_most(self):
        task_id = self.paused_task(why="failed", state="stuck")
        for _ in range(task_journal.MAX_TRIES):
            self.assertEqual(self.journal.claim(task_id)["state"], "resuming")
            self.journal.start("", resume_id=task_id)
            self.journal.pause(task_id, "stuck", "failed")
        entry = self.journal.claim(task_id)
        self.assertEqual((entry["state"], entry["resumes"]), ("dropped", 3))
        self.assertIsNone(self.journal.get(task_id))

    def test_only_picked_up_goes_that_got_stuck_count(self):
        task_id = self.paused_task(why="failed", state="stuck")
        for _ in range(5):  # stopped by the user each time: not a failed go
            self.assertEqual(self.journal.claim(task_id)["state"], "resuming")
            self.journal.start("", resume_id=task_id)
            self.journal.pause(task_id, "paused", "stop")
        entry = self.journal.get(task_id)
        self.assertEqual((entry["resumes"], entry["fails"]), (5, 0))
        for _ in range(task_journal.MAX_TRIES):
            self.assertEqual(self.journal.claim(task_id)["state"], "resuming")
            self.journal.start("", resume_id=task_id)
            self.journal.pause(task_id, "stuck", "unchanged")
        self.assertEqual(self.fresh_journal().get(task_id)["fails"], task_journal.MAX_TRIES)  # kept over a restart
        self.assertEqual(self.journal.claim(task_id)["state"], "dropped")
        self.assertIsNone(self.journal.get(task_id))

    def test_a_task_being_picked_up_cant_be_forgotten(self):
        task_id = self.paused_task()
        self.journal.claim(task_id)
        self.assertIsNone(self.journal.forget(task_id))
        self.assertEqual(self.journal.get(task_id)["state"], "resuming")
        self.journal.unclaim(task_id)
        self.assertEqual(self.journal.forget(task_id)["id"], task_id)

    def test_changes_of_state_wait_for_the_disk_and_step_notes_dont(self):
        with mock.patch.object(task_journal.os, "fsync") as fsync:
            task_id = self.journal.start("open the weather")
            self.assertEqual(fsync.call_count, 1)
            self.journal.mark_in_flight(task_id, {"type": "wait", "seconds": 1}, step=1)
            self.journal.mark_done_action(task_id, "waiting")
            self.journal.checkpoint(task_id, step=1, todo="look at the forecast")
            self.assertEqual(fsync.call_count, 1)
            self.journal.pause(task_id, "paused", "stop")
            self.journal.claim(task_id)
            self.journal.unclaim(task_id)
            self.journal.forget(task_id)
            self.assertEqual(fsync.call_count, 5)

    def test_the_ai_resting_keeps_its_retry_time(self):
        task_id = self.journal.start("summarize this page")
        entry = self.journal.pause(task_id, "paused", "ai_busy", retry_at=NOW + 150)
        self.assertEqual(entry["retry_at"], NOW + 150)
        self.assertEqual(task_journal.resting_minutes(entry, NOW), 3)
        self.assertEqual(task_journal.resting_minutes(entry, NOW + 149), 1)
        self.assertEqual(task_journal.resting_minutes(entry, NOW + 150), 0)
        claimed = self.journal.claim(task_id)
        self.assertEqual(claimed["resumes"], 1)
        self.journal.unclaim(task_id)  # still resting: refused, and that isn't a try
        self.assertEqual(self.journal.get(task_id)["resumes"], 0)
        other = self.journal.start("open the weather")
        self.journal.pause(other, "paused", "stop", retry_at=NOW + 99)
        self.assertIsNone(self.journal.get(other)["retry_at"])  # only a resting AI has a retry time
        self.assertEqual(task_journal.resting_minutes(self.journal.get(other), NOW), 0)

    def test_offer_forget_mark_offered_and_relabel(self):
        older = self.paused_task("open the weather", why="limit")
        self.now += 1
        newer = self.paused_task("send Dana the photos", why="newer")
        self.assertEqual(self.journal.offer()["id"], newer)
        self.assertTrue(self.journal.relabel(newer, "stop"))  # a recorded "stop" first looked like a newer command
        self.now += 600
        self.assertEqual(self.journal.offer()["id"], older)  # a stop is offered 10 minutes, not 30
        self.assertTrue(self.journal.mark_offered(older))
        self.assertTrue(self.journal.get(older)["offered"])
        self.assertEqual(self.journal.forget()["id"], older)
        self.assertIsNone(self.journal.forget())
        running = self.journal.start("open Notepad")
        self.assertIsNone(self.journal.forget(running))  # Stop is the way to end a running task
        self.assertFalse(self.journal.relabel(running, "stop"))
        self.assertFalse(self.journal.mark_offered(running))

    def test_a_finished_or_dropped_task_is_removed(self):
        done = self.journal.start("open the weather")
        dropped = self.journal.start("send it")
        self.assertIsNone(self.journal.pause(done, "done", ""))
        self.assertIsNone(self.journal.pause(dropped, "dropped", ""))
        self.assertEqual(self.on_disk()["tasks"], [])
        self.assertIsNone(self.journal.pause("nothere", "paused", "stop"))

    def test_a_new_task_parks_one_whose_thread_is_gone(self):
        stale = self.journal.start("open Notepad")
        self.journal.mark_in_flight(stale, "pressing Enter")
        fresh = self.journal.start("open the weather")
        entry = self.journal.get(stale)
        self.assertEqual((entry["state"], entry["why"], entry["unsure"]), ("stuck", "error", "pressing Enter"))
        self.assertEqual(self.journal.get(fresh)["state"], "running")

    def test_wrong_names_are_refused(self):
        task_id = self.journal.start("open the weather")
        with self.assertRaises(TypeError):
            self.journal.checkpoint(task_id, stepp=2)
        with self.assertRaises(ValueError):
            self.journal.pause(task_id, "sleeping", "stop")
        with self.assertRaises(ValueError):
            self.journal.pause(task_id, "paused", "bored")
        with self.assertRaises(ValueError):
            self.journal.recover("bored")

    def test_threads_share_the_journal_safely(self):
        task_id = self.journal.start("open the weather")

        def act(n):
            for k in range(10):
                self.journal.mark_in_flight(task_id, f"action {n}.{k}")
                self.journal.mark_done_action(task_id, f"done {n}.{k}")
                self.journal.offer()

        threads = [threading.Thread(target=act, args=(n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        entry = self.journal.get(task_id)
        self.assertEqual((len(entry["done"]), entry["in_flight"]), (40, ""))
        self.assertLessEqual(len(self.on_disk()["tasks"][0]["done"]), 40)  # the file is whole JSON


class JournalWordsTests(JournalTestCase):
    def test_for_ui_has_plain_words_for_every_reason(self):
        self.assertEqual(set(task_journal.WHY_WORDS), set(task_journal.REASONS))
        self.assertEqual(set(task_journal.WHY_FOR_PLANNER), set(task_journal.REASONS))
        for why in task_journal.REASONS:
            state = "stuck" if why in ("unchanged", "failed", "error") else "paused"
            task_id = self.paused_task(why=why, state=state)
            shown = TaskJournal.for_ui(self.journal.get(task_id))
            self.assertEqual(set(shown), {"id", "state", "why", "words", "step", "request", "title", "ask"})
            self.assertEqual((shown["state"], shown["why"], shown["ask"]), (state, why, "Resume task"))
            self.assertTrue(shown["words"].endswith("."), why)
            self.journal.forget(task_id)
        task_id = self.paused_task("send Dana the photos " * 10, step=3)
        shown = task_journal.for_ui(self.journal.get(task_id))
        self.assertEqual((shown["words"], shown["step"], len(shown["request"])), ("You said stop.", 3, 80))
        self.assertTrue(shown["title"].startswith("Paused at step 3: “send Dana the photos"))
        self.assertTrue(shown["title"].endswith("…”"))
        self.journal.forget(task_id)
        stuck = task_journal.for_ui(self.journal.get(self.paused_task("open Notepad", why="unchanged", state="stuck")))
        self.assertEqual((stuck["title"], stuck["words"]), ("Stuck at step 1: “open Notepad”", "The screen stopped changing."))

    def test_yes_send_it_only_while_the_send_is_still_held(self):
        entry = self.journal.get(self.paused_task(why="waiting_yes"))
        self.assertEqual(task_journal.for_ui(entry, send_waiting=True)["ask"], "Yes, send it")
        self.assertEqual(task_journal.for_ui(entry)["ask"], "Resume task")
        other = self.journal.get(self.paused_task(why="limit"))
        self.assertEqual(task_journal.for_ui(other, send_waiting=True)["ask"], "Resume task")

    def test_resume_context_tells_the_planner_what_is_done_and_what_was_left(self):
        history = [{"step": 3, "result": "Clicked Compose"}]
        task_id = self.journal.start("send Dana the photos")
        self.journal.checkpoint(task_id, step=3, done=["Opened Gmail.", "Clicked Compose"], todo="attach the photos.",
                                history=history, copied="Dana's address")
        self.journal.mark_in_flight(task_id, {"type": "click_element", "id": 4}, step=3, target="Send")
        self.journal.pause(task_id, "paused", "stop")
        seed = TaskJournal.resume_context(self.journal.claim(task_id))
        self.assertEqual(seed["note"], "This task was paused at step 3 (the user said stop). Already done: Opened Gmail; "
                                       "Clicked Compose. Your last note of what was left: attach the photos. Look at the "
                                       "screen now; it may have changed. Do not redo what is done. The last action "
                                       "(clicking “Send”) may or may not have happened; check the screen first.")
        self.assertEqual((seed["step_offset"], seed["done_at_start"], seed["unsure"]), (3, 2, "clicking “Send”"))
        self.assertEqual((seed["completed"], seed["history"], seed["copied"]),
                         (["Opened Gmail.", "Clicked Compose"], history, "Dana's address"))
        fresh = task_journal.resume_context({"id": "a", "why": "off", "step": 0})
        self.assertEqual(fresh["note"], "This task was paused at step 1 (the PC was off). Already done: nothing yet. "
                                        "Look at the screen now; it may have changed. Do not redo what is done.")
        self.assertEqual((fresh["step_offset"], fresh["completed"], fresh["unsure"]), (0, [], ""))

    def test_the_startup_notice(self):
        entry = {"id": "a", "request": "send Dana the photos", "why": "update"}
        self.assertEqual(task_journal.startup_words(entry),
                         "I updated myself in the middle of “send Dana the photos”. Say “keep going” or tap Resume task.")
        self.assertEqual(task_journal.startup_words(dict(entry, why="crash")),
                         "I was in the middle of “send Dana the photos” when I crashed and restarted. Say “keep going” "
                         "or tap Resume task.")
        self.assertEqual(task_journal.startup_words(dict(entry, why="off")),
                         "I was in the middle of “send Dana the photos” when the PC was off. Say “keep going” or tap "
                         "Resume task.")

    def test_action_words(self):
        words = task_journal.action_words
        self.assertEqual(words({"type": "type_text", "text": "Here you go", "press_enter": True}),
                         "typing 11 characters and pressing Enter")  # what is typed never goes in tasks.json
        self.assertEqual(words({"type": "type_text", "text": "x"}), "typing 1 character")
        self.assertEqual(words({"type": "type_text", "text": "Here you go", "press_enter": True}, show_text=True),
                         "typing “Here you go” and pressing Enter")  # a question asked now
        self.assertEqual(words({"type": "type_text", "text": "x" * 100}, show_text=True), "typing “" + "x" * 39 + "…”")
        self.assertEqual(words({"type": "search_web", "query": "cheap flights"}), "searching the web")
        self.assertEqual(words({"type": "search_web", "query": "cheap flights"}, show_text=True),
                         "searching for “cheap flights”")
        self.assertEqual(words({"type": "press_key", "key": "enter"}), "pressing Enter")
        self.assertEqual(words({"type": "press_key", "key": "tab", "presses": 3}), "pressing Tab 3 times")
        self.assertEqual(words({"type": "press_key", "key": "tab", "presses": float("inf")}), "pressing Tab")
        self.assertEqual(words({"type": "press_key"}), "pressing a key")
        self.assertEqual(words({"type": "hotkey", "keys": ["ctrl", "enter"]}), "pressing Ctrl+Enter")
        self.assertEqual(words({"type": "hotkey", "keys": "alt+f4"}), "pressing Alt+F4")
        self.assertEqual(words({"type": "click_element", "id": 3}, "Send"), "clicking “Send”")
        self.assertEqual(words({"type": "click_element", "id": 3}), "clicking a button")
        self.assertEqual(words({"type": "double_click", "x": 1, "y": 2}), "double-clicking on the screen")
        self.assertEqual(words({"type": "delete_file", "path": "C:\\Users\\Dana\\report.docx"}), "deleting report.docx")
        self.assertEqual(words({"type": "open_path", "path": "C:\\Users\\Dana\\Photos\\"}), "opening Photos")
        self.assertEqual(words({"type": "open_app", "app": "Gmail"}), "opening Gmail")
        self.assertEqual(words({"type": "close_window", "title": "Untitled - Notepad"}), "closing “Untitled - Notepad”")
        self.assertEqual(words({"type": "system_control", "command": "lock_pc"}), "locking the PC")
        self.assertEqual(words({"type": "system_control", "command": "volume_up"}), "pressing volume up")
        self.assertEqual(words({"type": "scroll", "direction": "up", "amount": 3}), "scrolling up")
        self.assertEqual(words({"type": "wait", "seconds": 1}), "waiting")
        self.assertEqual(words({"type": "something_new"}), "something new")


if __name__ == "__main__":
    unittest.main()
