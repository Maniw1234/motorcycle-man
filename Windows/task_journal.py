# The task journal: how far a long task got, kept in tasks.json, so a task cut off part way (Stop, a newer
# command, the AI resting, a crash, an update...) can be picked up with "keep going" or Resume task.
# A resume never replays anything. Motorcycle Man takes a fresh screenshot and plans again, told what is done,
# what was left and which action may have been cut off halfway, and it asks before anything that can't be undone.
# Pure Python (no Qt), so it is tested on its own. Use one TaskJournal per file in a process: it keeps the file
# in memory, because the phone asks for the status 8 times a second.
import copy
import json
import logging
import math
import ntpath
import os
import re
import secrets
import threading
import time

LOG = logging.getLogger("motorcycle_man")

VERSION = 1
LIVE = ("running", "resuming")  # a task thread has it, or a resume is about to start it
PARKED = ("paused", "stuck")    # waiting for the user: stopped from outside, or stuck by itself
STATES = LIVE + PARKED
REASONS = ("stop", "newer", "update", "update_failed", "quit", "crash", "off", "ai_busy", "limit", "no_answer",
           "waiting_yes", "unchanged", "failed", "error")
MAX_KEPT = 3      # parked tasks kept; the phone and the PC offer the newest
MAX_TRIES = 3     # picked-up goes that got stuck again; then the task is dropped (Stop and the like don't count)
MAX_DONE = 50     # done actions kept (MAX_TASK_ACTIONS is 50 a go)
MAX_HISTORY = 10  # planner steps kept: its prompt only ever shows history[-10:]
KEEP = {"stop": 600, "newer": 1800}  # seconds a parked task is offered: after Stop or a newer command, not long
KEEP_OTHER = 3 * 3600                # anything else: 3 hours
MAX_SENT = 10     # actions kept that can't be undone
FIELDS = ("step", "done", "todo", "history", "in_flight", "unsure", "sent", "copied")  # what checkpoint() takes
SECRET_FIELDS = ("text", "content", "query")  # what an action types, writes or searches for: only its length is kept

# Line 2 of the resume bar, the PC status and the startup notice.
WHY_WORDS = {
    "stop": "You said stop.", "newer": "You gave me something newer.", "update": "I updated myself.",
    "update_failed": "The update didn't work.",
    "quit": "The app was closed.", "crash": "I crashed and restarted.", "off": "The PC was off.",
    "ai_busy": "The AI needed a rest.", "limit": "It took too many steps.", "no_answer": "Nobody answered my question.",
    "waiting_yes": "Waiting for your yes to send.", "unchanged": "The screen stopped changing.",
    "failed": "A step didn't work.", "error": "Something went wrong.",
}
# The same, for the planner's resume note.
WHY_FOR_PLANNER = {
    "stop": "the user said stop", "newer": "the user gave a newer command", "update": "Motorcycle Man updated itself",
    "update_failed": "an update was tried and didn't work",
    "quit": "Motorcycle Man was closed", "crash": "Motorcycle Man crashed and restarted", "off": "the PC was off",
    "ai_busy": "the AI needed a rest", "limit": "it reached the time or step limit",
    "no_answer": "nobody answered a question",
    "waiting_yes": "it was holding the final send for the user's yes; the user has NOT said yes, so hold the final "
                   "send/post/run step again (awaiting_yes)",
    "unchanged": "the screen stopped changing", "failed": "a step didn't work", "error": "something went wrong",
}
# The executor adds what was copied to a step's result; only the length of it is kept on disk.
COPIED_NOTE = re.compile(r' \(copied (\d+) characters: ".*"\)$', re.DOTALL)
SYSTEM_WORDS = {"sleep": "putting the PC to sleep", "sleep_pc": "putting the PC to sleep", "lock": "locking the PC",
                "lock_pc": "locking the PC"}


def short(text, most: int) -> str:
    # One line of at most `most` characters, with … when it was cut.
    text = " ".join(str(text or "").split())
    return text if len(text) <= most else text[:most - 1].rstrip() + "…"


def _line(text, most: int) -> str:
    return " ".join(str(text or "").split())[:most]


# Words of a note that are never what was typed or copied.
PLAIN_WORDS = frozenset("about after again also back before box click code copy enter from have here into just like make "
                        "more next open over page paste press send some than that their them then there these they this "
                        "type what when where which will with your".split())


def scrub(note, said=(), request="") -> str:
    # The planner's note of what is left, without what the task typed, wrote, searched for or copied (said):
    # each of those, and each longer word or number in them, becomes "(text)". Words of the request stay (they
    # are kept anyway), and so do plain words like "code" or "send".
    note = str(note or "")
    known = set(re.findall(r"\w+", str(request or "").casefold())) | PLAIN_WORDS
    pieces = set()
    for text in said:
        text = " ".join(str(text or "")[:2000].split())
        if (len(text) >= 3 or re.search(r"\d", text)) and text.casefold() not in known:
            pieces.add(text)
        for word in re.findall(r"\w+", text):
            if re.search(r"\d", word) or (len(word) >= 4 and word.casefold() not in known):
                pieces.add(word)
    for piece in sorted(pieces, key=len, reverse=True):
        note = re.sub(r"(?<!\w)" + re.escape(piece) + r"(?!\w)", "(text)", note, flags=re.IGNORECASE)
    return note


def _number(value, default=None):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _value(value):
    # A field of an action, small enough to keep: long text is cut, nested things are left out.
    if isinstance(value, str):
        return value[:200]
    if isinstance(value, list):
        return [_value(v) for v in value[:10] if not isinstance(v, (dict, list))]
    return value if value is None or isinstance(value, (bool, int, float)) else None


def _hidden(value: str) -> str:
    # What was typed, written or searched for, as tasks.json keeps it: its length only.
    return f"<{len(value)} char{'' if len(value) == 1 else 's'}>"


def _clean_history(items) -> list:
    # The planner's last steps. What an action typed, wrote or searched for is kept as its length only, also where
    # the step's result quotes it ('Typed "<6 chars>"'), and results are cut short.
    kept = []
    for item in items[-MAX_HISTORY:] if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        clean = {}
        if _number(item.get("step")) is not None:
            clean["step"] = int(item["step"])
        result = COPIED_NOTE.sub(r" (copied \1 characters)", str(item.get("result") or ""))
        action = item.get("action")
        if isinstance(action, dict):
            clean["action"] = {k: _hidden(v) if k in SECRET_FIELDS and isinstance(v, str) else _value(v)
                               for k, v in action.items() if isinstance(k, str)}
            for name in SECRET_FIELDS:
                said = action.get(name)
                if isinstance(said, str) and said.strip():
                    hidden = _hidden(said)
                    # Longest first; the executor quotes up to 40 ('Typed "..."'). Never inside a word, so a short
                    # one like "e" doesn't eat the "e" of "Typed".
                    for piece in dict.fromkeys((said, said[:120], said[:80], said[:40])):
                        result = re.sub(r"(?<![0-9A-Za-z])" + re.escape(piece) + r"(?![0-9A-Za-z])",
                                        lambda _: hidden, result)
        clean["result"] = result[:120]
        kept.append(clean)
    return kept


def _clean_done(items) -> list:
    return [_line(d, 250) for d in items if isinstance(d, str)][-MAX_DONE:] if isinstance(items, list) else []


def _clean_routine(routine):
    # {id, name, steps, at}: the routine a halted step came from, so a resume can run the rest of it.
    if not isinstance(routine, dict) or not isinstance(routine.get("id"), str) \
            or not isinstance(routine.get("steps"), list):
        return None
    return {"id": routine["id"][:16], "name": _line(routine.get("name"), 60),
            "steps": [str(s)[:300] for s in routine["steps"][:12]], "at": max(0, int(_number(routine.get("at"), 0)))}


def _clean_fields(fields: dict) -> dict:
    clean = {}
    for name, value in fields.items():
        if name == "step":
            clean[name] = max(0, int(_number(value, 0)))
        elif name == "done":
            clean[name] = _clean_done(value)
        elif name == "history":
            clean[name] = _clean_history(value)
        elif name == "todo":
            clean[name] = _line(value, 200)
        elif name in ("in_flight", "unsure"):
            clean[name] = _line(value, 160)
        elif name == "sent":
            clean[name] = [_line(d, 120) for d in value if isinstance(d, str)][-MAX_SENT:] if isinstance(value, list) else []
        elif name == "copied":
            clean[name] = str(value or "")[:300]  # kept in memory only: never saved
    return clean


def _blank(task_id: str, request, source, routine, now: float) -> dict:
    return {"id": task_id, "request": _line(request, 500), "source": _line(source, 20) or "typed", "state": "running",
            "why": "", "started": now, "updated": now, "paused_at": None, "step": 0, "done": [], "todo": "",
            "history": [], "in_flight": "", "unsure": "", "sent": [], "resumes": 0, "fails": 0, "offered": False,
            "retry_at": None,
            "routine": routine, "copied": ""}


def _clean(item):
    # One entry read from tasks.json, every field the right type; None when it can't be used.
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] \
            or item.get("state") not in STATES:
        return None
    entry = _blank(item["id"][:16], item.get("request"), item.get("source"), _clean_routine(item.get("routine")),
                   _number(item.get("started"), 0))
    entry.update(_clean_fields({k: item.get(k) for k in FIELDS if k != "copied"}))
    entry.update(state=item["state"], why=item.get("why") if item.get("why") in REASONS else "",
                 updated=_number(item.get("updated"), entry["started"]), paused_at=_number(item.get("paused_at")),
                 resumes=max(0, int(_number(item.get("resumes"), 0))),
                 fails=min(MAX_TRIES, max(0, int(_number(item.get("fails"), 0)))),
                 offered=item.get("offered") is True, retry_at=_number(item.get("retry_at")))
    if item.get("was") in PARKED:
        entry["was"] = item["was"]
    if entry["state"] in PARKED and entry["paused_at"] is None:
        entry["paused_at"] = entry["updated"]
    return entry


def expired(entry: dict, now: float = None) -> bool:
    # A parked task is offered for 10 minutes after Stop, 30 after a newer command, else 3 hours.
    if entry.get("state") not in PARKED:
        return False
    now = time.time() if now is None else now
    return now - (entry.get("paused_at") or 0) >= KEEP.get(entry.get("why"), KEEP_OTHER)


def resting_minutes(entry: dict, now: float = None) -> int:
    # Minutes until the AI may be asked again, for a task paused while it rested; 0 when it can go now.
    left = _number(entry.get("retry_at"), 0) - (time.time() if now is None else now)
    return max(1, math.ceil(left / 60)) if entry.get("why") == "ai_busy" and left > 0 else 0


def _file_name(path: str) -> str:
    # A path's last part ("report.docx"), so the in-flight note stays short.
    return ntpath.basename(path.rstrip("\\/")) or path


def _key_name(key) -> str:
    key = str(key or "").strip()
    return "a key" if not key else key.upper() if len(key) == 1 else key[:1].upper() + key[1:].lower()


def action_words(action: dict, target: str = "", show_text: bool = False) -> str:
    # What an action does, in plain words, for the in-flight note and the done list: "typing 7 characters and
    # pressing Enter". What it types or searches for is left out (tasks.json is kept on disk), unless show_text
    # (a question asked now, never saved). target is the name of the control a click_element clicks, when known.
    kind = str(action.get("type") or "")
    if kind == "type_text":
        text = str(action.get("text") or "")
        words = (f"typing “{short(text, 40)}”" if show_text
                 else f"typing {len(text)} character{'' if len(text) == 1 else 's'}")
        return words + (" and pressing Enter" if action.get("press_enter") is True else "")
    if kind in ("press_key", "key_down", "key_up"):
        key = _key_name(action.get("key"))
        if kind == "press_key":
            try:
                presses = int(action.get("presses") or 1)
            except (TypeError, ValueError, OverflowError):
                presses = 1
            return f"pressing {key}" + (f" {presses} times" if presses > 1 else "")
        return f"holding {key}" if kind == "key_down" else f"letting go of {key}"
    if kind == "hotkey":
        keys = action.get("keys")
        keys = keys.split("+") if isinstance(keys, str) else keys if isinstance(keys, list) else []
        return "pressing " + "+".join(_key_name(k) for k in keys)
    if kind == "click_element":
        return f"clicking “{short(target, 40)}”" if target else "clicking a button"
    if kind in ("click", "double_click", "right_click", "mouse_down", "mouse_up"):
        verb = {"double_click": "double-clicking", "right_click": "right-clicking"}.get(kind, "clicking")
        return verb + " on the screen"
    if kind in ("open_app", "open_url", "open_path"):
        what = str(action.get("app") or action.get("url") or action.get("path") or "")
        return "opening " + short(_file_name(what) if kind == "open_path" else what, 60)
    if kind in ("focus_window", "minimize_window", "maximize_window", "close_window"):
        verb = {"focus_window": "switching to", "minimize_window": "minimizing", "maximize_window": "maximizing",
                "close_window": "closing"}[kind]
        return f"{verb} “{short(action.get('title'), 40)}”"
    if kind in ("create_folder", "write_file", "delete_file", "copy_file", "move_file", "rename_file"):
        verb = {"create_folder": "making the folder", "write_file": "writing", "delete_file": "deleting",
                "copy_file": "copying", "move_file": "moving", "rename_file": "renaming"}[kind]
        return f"{verb} {short(_file_name(str(action.get('path') or action.get('source') or '')), 60)}"
    if kind == "system_control":
        command = str(action.get("command") or "").lower()
        return SYSTEM_WORDS.get(command, "pressing " + command.replace("_", " "))
    if kind == "search_web":
        return f"searching for “{short(action.get('query'), 40)}”" if show_text else "searching the web"
    if kind == "scroll":
        return f"scrolling {action.get('direction') or 'down'}"
    return {"drag": "dragging on the screen", "move_mouse": "moving the mouse", "wait": "waiting",
            "point_at": "pointing", "research": "doing research"}.get(kind, kind.replace("_", " ") or "working")


def for_ui(entry: dict, send_waiting: bool = False) -> dict:
    # What the phone's resume bar, the PC window and the tray show for a parked task. send_waiting: the held
    # send can still be finished with a yes (PENDING_SEND is live), so the button says "Yes, send it".
    step = max(1, int(entry.get("step") or 0))
    stuck = entry.get("state") == "stuck"
    request = short(entry.get("request"), 80)
    return {"id": entry["id"], "state": "stuck" if stuck else "paused", "why": entry.get("why") or "",
            "words": WHY_WORDS.get(entry.get("why"), WHY_WORDS["error"]), "step": step, "request": request,
            "title": f"{'Stuck' if stuck else 'Paused'} at step {step}: “{request}”",
            "ask": "Yes, send it" if send_waiting and entry.get("why") == "waiting_yes" else "Resume task"}


def resume_context(entry: dict) -> dict:
    # The seed for the planner loop when a task is picked up: what it knew when it stopped, and a note telling
    # it to look at the screen again and not redo what is done. The caller starts its step count at step_offset.
    step = int(entry.get("step") or 0)
    done = list(entry.get("done") or [])
    listed = [_line(d, 120).rstrip(" .") for d in done[-10:]]
    todo = _line(entry.get("todo"), 200).rstrip(" .")
    unsure = _line(entry.get("unsure"), 160)
    why = WHY_FOR_PLANNER.get(entry.get("why"), WHY_FOR_PLANNER["error"])
    note = (f"This task was paused at step {max(1, step)} ({why}). "
            f"Already done: {'; '.join(listed) if listed else 'nothing yet'}. "
            + (f"Your last note of what was left: {todo}. " if todo else "")
            + "Look at the screen now; it may have changed. Do not redo what is done.")
    if unsure:
        note += f" The last action ({unsure}) may or may not have happened; check the screen first."
    sent = [s for s in entry.get("sent") or [] if isinstance(s, str)]
    if sent:
        listed = "; ".join(_line(s, 120).rstrip(" .") for s in sent[-5:])
        note += (f" Already sent or not undoable: {listed}. Do NOT do these again unless the screen shows they did "
                 "not happen.")
    return {"history": copy.deepcopy(entry.get("history") or []), "completed": done, "todo": entry.get("todo") or "",
            "copied": entry.get("copied") or "", "step_offset": step, "done_at_start": len(done), "unsure": unsure,
            "sent": sent, "note": note}


def startup_words(entry: dict) -> str:
    # The one notice at start for a task an update, Quit, a crash or a power cut cut off.
    task = f"“{short(entry.get('request'), 60)}”"
    if entry.get("why") == "update":
        return f"I updated myself in the middle of {task}. Say “keep going” or tap Resume task."
    reason = WHY_WORDS.get(entry.get("why"), WHY_WORDS["error"]).rstrip(".")
    if not reason.startswith("I "):
        reason = reason[:1].lower() + reason[1:]
    return f"I was in the middle of {task} when {reason}. Say “keep going” or tap Resume task."


class TaskJournal:
    for_ui = staticmethod(for_ui)
    resume_context = staticmethod(resume_context)

    def __init__(self, path, clock=time.time):
        self.path = path    # a function giving tasks.json's path (it follows APPDATA, which every test changes)
        self.clock = clock  # wall-clock seconds, since tasks.json outlives a restart
        self.lock = threading.Lock()
        self._where = None  # the file self._items was read from
        self._file = ""
        self._items = []
        self._failing = False  # the last save failed: the log says so once, not 3 times an action

    # -- the file --------------------------------------------------------------------------------------------

    def _load(self) -> list:
        path = self.path()
        where = os.path.normcase(os.path.abspath(path))
        if where != self._where:
            self._where, self._file, self._items = where, path, []
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = None  # none yet, or broken: the next save writes a good one
            tasks = data.get("tasks") if isinstance(data, dict) and data.get("v") == VERSION else None
            for item in tasks if isinstance(tasks, list) else []:
                try:
                    entry = _clean(item)
                except (TypeError, ValueError, OverflowError):
                    entry = None
                if entry and not any(t["id"] == entry["id"] for t in self._items):
                    self._items.append(entry)
            read = len(self._items)
            self._tidy()
            if len(self._items) != read:
                self._save()  # expired ones are gone from the file too
        return self._items

    def _save(self, durable: bool = False) -> bool:
        # The same .part + os.replace write as routines.json, so a crash mid-write never leaves half a file.
        # A failed write only costs the resume: the task itself carries on. durable: a change of state (started,
        # parked, picked up, forgotten) waits until it is on the disk, so a power cut can't lose it; the notes
        # written at every step don't wait.
        self._tidy()
        part = self._file + ".part"
        tasks = [{k: v for k, v in t.items() if k != "copied"} for t in self._items]
        try:
            with open(part, "w", encoding="utf-8") as f:
                json.dump({"v": VERSION, "tasks": tasks}, f, ensure_ascii=False, indent=1)
                if durable:
                    f.flush()
                    os.fsync(f.fileno())
            os.replace(part, self._file)
            self._failing = False
            return True
        except (OSError, TypeError, ValueError) as exc:
            if not self._failing:
                LOG.warning("Could not save the task journal: %s", exc)
            self._failing = True
            try:
                os.remove(part)
            except OSError:
                pass
            return False

    def _tidy(self):
        # Drops parked tasks that are past their time, and all but the newest MAX_KEPT.
        now = self.clock()
        parked = sorted((t for t in self._items if t["state"] in PARKED and not expired(t, now)),
                        key=lambda t: t["paused_at"])
        kept = {id(t) for t in parked[-MAX_KEPT:]}
        self._items = [t for t in self._items if t["state"] in LIVE or id(t) in kept]

    def _find(self, task_id):
        return next((t for t in self._load() if t["id"] == task_id), None) if task_id else None

    def _newest(self):
        now = self.clock()
        parked = [t for t in self._load() if t["state"] in PARKED and not expired(t, now)]
        return max(parked, key=lambda t: t["paused_at"], default=None)

    def _park(self, entry: dict, state: str, why: str, now: float):
        # An action cut off halfway may or may not have happened: it becomes "unsure", and a resume asks
        # before the first thing it does that can't be undone.
        entry.pop("was", None)
        entry.update(state=state, why=why, paused_at=now, updated=now, unsure=entry["in_flight"] or entry["unsure"],
                     in_flight="", offered=False, retry_at=None)

    # -- a task thread ---------------------------------------------------------------------------------------

    def start(self, request, source="typed", routine=None, resume_id=None) -> str:
        # A new entry, "running"; or, with resume_id, the claimed entry running again with all it knew.
        with self.lock:
            now = self.clock()
            entry = self._find(resume_id)
            for other in list(self._load()):
                if other["state"] == "running" and other is not entry:
                    self._park(other, "stuck", "error", now)  # its thread is gone: only one task runs at a time
            if entry is None:
                # The same command again: an older go at it that stopped halfway isn't offered any more (picking
                # it up after this one could do it twice).
                same = _line(request, 500).casefold()
                if same:
                    self._items = [t for t in self._items
                                   if t["state"] not in PARKED or _line(t.get("request"), 500).casefold() != same]
                task_id = secrets.token_hex(4)
                while self._find(task_id):
                    task_id = secrets.token_hex(4)
                entry = _blank(task_id, request, source, _clean_routine(routine), now)
                self._items.append(entry)
            else:
                entry.pop("was", None)
                entry.update(state="running", why="", paused_at=None, retry_at=None, offered=False, updated=now)
                if routine is not None:
                    entry["routine"] = _clean_routine(routine)
            self._save(durable=True)
            return entry["id"]

    def checkpoint(self, task_id, **fields) -> bool:
        # Saves step, done, todo, history, in_flight, unsure (copied stays in memory). Only while the entry is
        # still running: a late write from a stopped thread never brings back a task that was parked or forgotten.
        unknown = set(fields) - set(FIELDS)
        if unknown:
            raise TypeError("checkpoint() got unknown fields: " + ", ".join(sorted(unknown)))
        with self.lock:
            entry = self._find(task_id)
            if entry is None or entry["state"] != "running":
                return False
            entry.update(_clean_fields(fields), updated=self.clock())
            return self._save()

    def mark_in_flight(self, task_id, action, step=None, target="") -> bool:
        # Written just before an action runs (an action dict, or words). step is the step count over all goes.
        fields = {"in_flight": action_words(action, target) if isinstance(action, dict) else action}
        if step is not None:
            fields["step"] = step
        return self.checkpoint(task_id, **fields)

    def mark_done_action(self, task_id, summary, sent=None) -> bool:
        # Written just after an action returns: what it did joins the done list and nothing is in flight. sent:
        # the actions so far that can't be undone (a send, a delete...), when this one was such an action.
        with self.lock:
            entry = self._find(task_id)
            if entry is None or entry["state"] != "running":
                return False
            entry.update(done=_clean_done(entry["done"] + [str(summary)]), in_flight="", updated=self.clock())
            if sent is not None:
                entry.update(_clean_fields({"sent": sent}))
            return self._save()

    def pause(self, task_id, state, why, retry_at=None):
        # The task halted: "paused" (from outside, or waiting for the user) or "stuck" (it couldn't go on).
        # "done" or "dropped" removes it. A task already parked by recover() keeps recover's reason.
        if state not in PARKED + ("done", "dropped"):
            raise ValueError(f"unknown task state {state!r}")
        if state in PARKED and why not in REASONS:
            raise ValueError(f"unknown reason {why!r}")
        with self.lock:
            entry = self._find(task_id)
            if entry is None:
                return None
            if state in ("done", "dropped"):
                self._items.remove(entry)
                self._save(durable=True)
                return None
            if entry["state"] in LIVE:
                if state == "stuck" and entry["resumes"] > 0:
                    entry["fails"] += 1  # a picked-up go that got stuck again: these count toward MAX_TRIES
                self._park(entry, state, why, self.clock())
                if why == "ai_busy":
                    entry["retry_at"] = _number(retry_at)
            self._save(durable=True)
            return copy.deepcopy(entry)

    def finish(self, task_id):
        return self.pause(task_id, "done", "")

    # -- the user picking it up, or not ----------------------------------------------------------------------

    def claim(self, task_id=None):
        # Takes a parked task (task_id, or the newest) for a resume, once: a second tap gets None. After
        # MAX_TRIES picked-up goes that got stuck the task is dropped instead, and the copy returned has state
        # "dropped". resumes counts every go, for the log.
        with self.lock:
            entry = self._find(task_id) if task_id else self._newest()
            if entry is None or entry["state"] not in PARKED or expired(entry, self.clock()):
                return None
            if entry["fails"] >= MAX_TRIES:
                self._items.remove(entry)
                self._save(durable=True)
                return dict(copy.deepcopy(entry), state="dropped")
            entry.update(was=entry["state"], state="resuming", resumes=entry["resumes"] + 1, updated=self.clock())
            self._save(durable=True)
            return copy.deepcopy(entry)

    def unclaim(self, task_id) -> bool:
        # The resume didn't start (the AI is still resting, say): the task is parked as before, and that
        # doesn't count as a try.
        with self.lock:
            entry = self._find(task_id)
            if entry is None or entry["state"] != "resuming":
                return False
            entry.update(state=entry.pop("was", "paused"), resumes=max(0, entry["resumes"] - 1))
            self._save(durable=True)
            return True

    def recover(self, why) -> list:
        # Update, Quit, a crash or a power cut: a running task is parked, and its action in flight is unsure.
        # Returns only the entries this changed.
        if why not in REASONS:
            raise ValueError(f"unknown reason {why!r}")
        with self.lock:
            now = self.clock()
            found = [t for t in self._load() if t["state"] in LIVE]
            for entry in found:
                self._park(entry, "paused", why, now)
            if found:
                self._save(durable=True)
            return [copy.deepcopy(t) for t in found]

    def recover_after_restart(self, argv) -> list:
        # At start: a task still running in tasks.json was cut off by an update, a crash or a power cut.
        return self.recover("update" if "--updated" in argv else "crash" if "--restarted" in argv else "off")

    def offer(self):
        # The newest parked task that is still offered, or None. The file is read only the first time, so the
        # phone's status (8 times a second) costs nothing.
        with self.lock:
            entry = self._newest()
            return copy.deepcopy(entry) if entry else None

    def get(self, task_id):
        with self.lock:
            entry = self._find(task_id)
            return copy.deepcopy(entry) if entry else None

    def mark_offered(self, task_id) -> bool:
        # The startup notice for it was shown: it isn't shown again.
        with self.lock:
            entry = self._find(task_id)
            if entry is None or entry["state"] not in PARKED:
                return False
            entry["offered"] = True
            self._save()
            return True

    def relabel(self, task_id, why) -> bool:
        # A parked task's reason, put right: a recorded "stop" first looked like a newer command.
        if why not in REASONS:
            raise ValueError(f"unknown reason {why!r}")
        with self.lock:
            entry = self._find(task_id)
            if entry is None or entry["state"] not in PARKED:
                return False
            entry["why"] = why
            self._save()
            return True

    def forget(self, task_id=None):
        # Drops a parked task (task_id, or the one on offer). Returns the dropped entry, or None. One that is running
        # or being picked up is refused: Stop is the way to end it.
        with self.lock:
            entry = self._find(task_id) if task_id else self._newest()
            if entry is None or entry["state"] in LIVE:
                return None
            self._items.remove(entry)
            self._save(durable=True)
            return copy.deepcopy(entry)

    def expire(self) -> int:
        # Removes parked tasks past their time from the file; returns how many.
        with self.lock:
            now = self.clock()
            gone = [t for t in self._load() if expired(t, now)]
            if gone:
                self._save()
            return len(gone)
