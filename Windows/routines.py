# Routines: a name for a few commands done one after another, like "Good morning: open Gmail, then open the
# weather", run by saying the name (or "run good morning"), from the phone's Routines sheet, or on a
# schedule ("every weekday at 8 am", kept with the reminders in reminders.json). They are kept in
# routines.json.
import json, os, re, secrets, threading, time

import reminders

LEAD = reminders.LEAD
_NAME = r"[\"“']?(?P<name>[^\"”':]+?)[\"”']?"
MAKE = re.compile(rf"(?:make|create|add|set up|new|save)\s+(?:a\s+|my\s+)?(?:new\s+)?routine\s+(?:called\s+|named\s+)?{_NAME}"
                  r"\s*(?::|,|\bthat\b|\bwhich\b|\bto\b|\bwith\b|\bwhere you\b|\bwhere\b)\s*(?P<steps>.+)", re.IGNORECASE)
LIST = re.compile(r"(?:what(?:'s| are| is)?|show(?: me)?|list|which)\s+(?:are\s+)?(?:my\s+|the\s+)?routines?(?:\s+(?:do i have|are there))?",
                  re.IGNORECASE)
DELETE = re.compile(rf"(?:delete|remove|forget)\s+(?:my\s+|the\s+)?(?:routine\s+)?{_NAME}(?:\s+routine)?", re.IGNORECASE)
UNSCHEDULE = re.compile(rf"(?:unschedule|stop scheduling|don't run|do not run)\s+(?:my\s+|the\s+)?{_NAME}(?:\s+routine)?"
                        r"(?:\s+(?:automatically|on (?:a|its) schedule|any ?more))*", re.IGNORECASE)
RUN = re.compile(rf"(?:run|start|do|begin|play)\s+(?:my\s+|the\s+)?{_NAME}(?:\s+routine)?", re.IGNORECASE)
STEP_BREAK = re.compile(r"\s*(?:;|\n|,\s*(?:and\s+)?(?:then\s+)?|\s+and then\s+|\s+then\s+)\s*", re.IGNORECASE)
VERBS = {"open", "play", "check", "show", "start", "close", "mute", "pause", "read", "turn", "set", "launch", "search",
         "research", "send", "lock", "put", "find", "go", "scroll", "type", "resume", "skip", "make", "tell"}
STOPPED = ("Okay, I stopped", "Stopped for", "Skipped for", "I'm still stopping", "Error", "Okay, I won't")


def plain(name: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", str(name).lower()))


def steps_from(text: str) -> list:
    # "opens gmail, then plays music" -> ["open gmail", "play music"].
    steps = []
    for part in STEP_BREAK.split(text):
        part = part.strip(" .!")
        first, _, rest = part.partition(" ")
        if first.lower().endswith("es") and first.lower()[:-2] in VERBS:
            part = (first[:-2] + " " + rest).strip()  # "goes" -> "go"
        elif first.lower().endswith("s") and first.lower()[:-1] in VERBS:
            part = (first[:-1] + " " + rest).strip()  # "opens" -> "open"
        if part:
            steps.append(part[:300])
    return steps[:12]


class RoutineBook:
    def __init__(self, path):
        self.path = path  # a function giving routines.json's path
        self.lock = threading.Lock()

    def _load(self) -> list:
        try:
            with open(self.path(), encoding="utf-8") as f:
                items = json.load(f)
        except (OSError, ValueError):
            return []
        return [r for r in items if isinstance(r, dict) and r.get("name") and isinstance(r.get("steps"), list)] \
            if isinstance(items, list) else []

    def _save(self, items: list):
        path = self.path()
        with open(path + ".part", "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        os.replace(path + ".part", path)

    def all(self) -> list:
        with self.lock:
            return self._load()

    def find(self, name_or_id: str):
        wanted = plain(name_or_id)
        return next((r for r in self.all() if r["id"] == name_or_id or plain(r["name"]) == wanted), None)

    def save(self, name: str, steps: list) -> dict:
        # A new routine, or new steps for the one with that name.
        name = " ".join(str(name).split())[:60]
        name = name[:1].upper() + name[1:]
        with self.lock:
            items = self._load()
            old = next((r for r in items if plain(r["name"]) == plain(name)), None)
            routine = {"id": old["id"] if old else secrets.token_hex(4), "name": name, "steps": list(steps)[:12],
                       "made": old.get("made", time.time()) if old else time.time()}
            self._save([r for r in items if r is not old] + [routine])
        return routine

    def delete(self, routine_id: str) -> bool:
        with self.lock:
            items = self._load()
            kept = [r for r in items if r["id"] != routine_id]
            if len(kept) != len(items):
                self._save(kept)
        return len(kept) != len(items)


def schedule(routine: dict, repeat, book: reminders.ReminderBook, now: float = None):
    # Runs the routine on that schedule from now on (or never, with repeat None), replacing any old one.
    book.cancel(lambda r: r.get("kind") == "routine" and r.get("routine") == routine["id"])
    if repeat:
        book.add(routine["name"], reminders.next_time(repeat, time.time() if now is None else now), "routine",
                 repeat=repeat, routine=routine["id"])


def schedule_of(routine: dict, book: reminders.ReminderBook):
    return next((r.get("repeat") for r in book.upcoming() if r.get("kind") == "routine" and r.get("routine") == routine["id"]), None)


def describe(routine: dict, book: reminders.ReminderBook) -> str:
    repeat = schedule_of(routine, book)
    return f"{routine['name']}: {', then '.join(routine['steps'])}" + (f" ({reminders.repeat_words(repeat)})" if repeat else "")


def handle(text: str, book: RoutineBook, schedules: reminders.ReminderBook, now: float = None):
    # ("reply", words) or ("run", routine) when text is about routines, else None.
    now = time.time() if now is None else now
    said = LEAD.sub("", " ".join(str(text).split())).strip(" .!?")
    if not said:
        return None
    made = MAKE.fullmatch(said)
    if made:
        repeat, steps_text = reminders.repeat_from(made["steps"])
        steps = steps_from(steps_text)
        if not steps:
            return "reply", "What should the routine do? Say something like \"make a routine called good morning: open Gmail, then open the weather\"."
        routine = book.save(made["name"], steps)
        schedule(routine, repeat, schedules, now)
        when = f" It runs {reminders.repeat_words(repeat)}." if repeat else ""
        return "reply", f"Saved the routine \"{routine['name']}\": {', then '.join(steps)}.{when} Say \"{routine['name']}\" to run it."
    if LIST.fullmatch(said):
        routines = book.all()
        if not routines:
            return "reply", "You have no routines. Say \"make a routine called good morning: open Gmail, then open the weather\"."
        return "reply", "Your routines:\n" + "\n".join(describe(r, schedules) for r in routines)
    for pattern in (UNSCHEDULE, DELETE):
        found = pattern.fullmatch(said)
        routine = book.find(found["name"]) if found else None
        if routine:
            schedule(routine, None, schedules)
            if pattern is DELETE:
                book.delete(routine["id"])
                return "reply", f"Deleted the routine \"{routine['name']}\"."
            return "reply", f"\"{routine['name']}\" won't run on its own any more."
    # Scheduling: "run good morning every weekday at 8", "schedule my good morning routine every day at 7".
    repeat, left = reminders.repeat_from(said)
    if repeat:
        words = re.sub(r"^(?:schedule|run|start|do)\s+(?:my\s+|the\s+)?", "", left.strip(), flags=re.IGNORECASE)
        routine = book.find(re.sub(r"(?:\s+(?:routine|for|at|on))+$", "", words, flags=re.IGNORECASE))
        if routine:
            schedule(routine, repeat, schedules, now)
            return "reply", f"\"{routine['name']}\" will run {reminders.repeat_words(repeat)}."
        return None
    # Running: "good morning", "run good morning", "start my good morning routine".
    run = RUN.fullmatch(said)
    routine = (book.find(run["name"]) if run else None) or book.find(re.sub(r"\s+routine$", "", said, flags=re.IGNORECASE))
    return ("run", routine) if routine else None
