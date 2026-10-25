# Reminders and timers: "remind me in 20 minutes to stretch", "remind me at 6 pm to call Dana", "remind me
# tomorrow to pay rent", "remind me every weekday at 8 to take my pills", "set a timer for 5 minutes". They
# are understood here, without Gemini, so they work at once and offline, and are kept in reminders.json
# until they go off (a repeating one stays, due again next time). Scheduled routines are kept here too.
import datetime as dt, json, os, re, secrets, threading, time

LATE_LIMIT = 12 * 3600  # a reminder missed by more than this while the PC was off is dropped
LATE_LIMITS = {"routine": 1800}  # but a routine (say, the morning one) only runs up to half an hour late
NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "half an": 0.5, "half a": 0.5, "a couple of": 2,
    "a couple": 2, "couple of": 2, "a few": 3, "few": 3,
}
UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60,
         "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600, "day": 86400, "days": 86400}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_NUMBER = r"\d+(?:\.\d+)?|" + "|".join(sorted((re.escape(w) for w in NUMBER_WORDS), key=len, reverse=True))
_UNIT = r"seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|[smh]"
DURATION = re.compile(rf"\b(?:in|after)\s+(?P<n>{_NUMBER})\s*(?P<u>{_UNIT})\b"
                      rf"(?:\s+(?:and\s+)?(?:(?P<half>a half)|(?P<n2>{_NUMBER})\s*(?P<u2>{_UNIT})\b))?", re.IGNORECASE)
CLOCK = re.compile(r"\b(?:at\s+)?(?:(?P<noon>noon|midday|midnight)|(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap]\.?\s?m\.?)"
                   r"|at\s+(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?(?:\s*o'?clock)?)(?=\W|$)", re.IGNORECASE)
DAY = re.compile(r"\b(?P<day>tomorrow(?: morning| afternoon| evening| night)?|tonight|this (?:morning|afternoon|evening)|"
                 r"(?:on |next )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b", re.IGNORECASE)
PART_OF_DAY = {"morning": 9, "afternoon": 15, "evening": 19, "night": 20, "tonight": 20}
_WEEKDAY = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?"
REPEAT = re.compile(rf"\b(?:every\s+(?P<every>day|weekday|weekend|morning|evening|night|{_WEEKDAY}(?:\s*(?:,|and|&)\s*{_WEEKDAY})*)"
                    rf"|(?P<daily>daily)|(?:on\s+)?(?P<on>weekdays|weekends)|each\s+(?P<each>day|morning|evening|night))\b", re.IGNORECASE)
REPEAT_TIMES = {"morning": (8, 0), "evening": (19, 0), "night": (21, 0)}
LEAD = re.compile(r"^(?:(?:hey|ok|okay),?\s+)?(?:motorcycle man,?\s+)?(?:please\s+|can you\s+|could you\s+|would you\s+)*", re.IGNORECASE)
REMIND = re.compile(r"(?:remind me|set (?:a |an )?reminder|add (?:a )?reminder|reminder)\b[:,]?\s*(?P<rest>.*)", re.IGNORECASE)
TIMER = re.compile(r"(?:(?:set|start)\s+(?:a\s+|an\s+)?)?(?:timer\s+(?:for\s+)?(?P<a>.+)|(?P<b>.+?)\s+timer)", re.IGNORECASE)
LIST = re.compile(r"(?:what(?:'s| are| is)?|show(?: me)?|list|any|do i have(?: any)?)\s+(?:are\s+)?(?:my\s+|the\s+)?"
                  r"(?:reminders?|timers?)(?:\s+(?:do i have|are there|set))?", re.IGNORECASE)
CANCEL = re.compile(r"(?:cancel|delete|remove|clear|stop|forget)\s+(?P<all>all\s+)?(?:(?:my|the)\s+)?"
                    r"(?P<what>reminders?|timers?)(?:\s+(?:to|about|for)\s+(?P<about>.+))?", re.IGNORECASE)


def number(text: str) -> float:
    text = " ".join(text.lower().split())
    return float(text) if re.fullmatch(r"\d+(?:\.\d+)?", text) else float(NUMBER_WORDS[text])


def seconds_in(match) -> float:
    seconds = number(match["n"]) * UNITS[match["u"].lower()]
    if match["half"]:
        seconds += 0.5 * UNITS[match["u"].lower()]
    elif match["n2"]:
        seconds += number(match["n2"]) * UNITS[match["u2"].lower()]
    return seconds


def duration_words(seconds: float) -> str:
    seconds = round(seconds)
    parts = []
    for size, name in ((86400, "day"), (3600, "hour"), (60, "minute"), (1, "second")):
        if seconds >= size and len(parts) < 2:
            count, seconds = divmod(seconds, size)
            parts.append(f"{count} {name}{'' if count == 1 else 's'}")
    return " and ".join(parts) or "0 seconds"


def clock_words(when: float, now: float = None) -> str:
    # "5:30 PM", "tomorrow at 9:00 AM", "Friday at 3:00 PM", or "Oct 30 at 3:00 PM".
    moment, today = dt.datetime.fromtimestamp(when), dt.datetime.fromtimestamp(now or time.time()).date()
    clock = moment.strftime("%I:%M %p").lstrip("0")
    days = (moment.date() - today).days
    if days == 0:
        return clock
    if days == 1:
        return f"tomorrow at {clock}"
    if 1 < days < 7:
        return f"{moment.strftime('%A')} at {clock}"
    if days == -1:
        return f"yesterday at {clock}"
    return f"{moment.strftime('%b')} {moment.day} at {clock}"


def _clock_time(match):
    # The hour and minute a clock match names, and whether am/pm was said.
    if match["noon"]:
        return (0 if match["noon"].lower() == "midnight" else 12), 0, True
    hour, minute = int(match["h"] or match["h2"]), int(match["m"] or match["m2"] or 0)
    ap = (match["ap"] or "").lower().replace(".", "").replace(" ", "")
    if hour > 23 or minute > 59 or (ap and not 1 <= hour <= 12):
        raise ValueError("no such time")
    if ap == "pm" and hour < 12:
        hour += 12
    elif ap == "am" and hour == 12:
        hour = 0
    return hour, minute, bool(ap) or hour > 12 or hour == 0


def when_from(text: str, now: float):
    # (when it is due, the text left once the time words are taken out), or (None, text).
    found = DURATION.search(text)
    if found:
        return now + seconds_in(found), text[:found.start()] + " " + text[found.end():]
    base = dt.datetime.fromtimestamp(now)
    day, clock = DAY.search(text), CLOCK.search(text)
    if not day and not clock:
        return None, text
    target, hour, minute, sure = base.date(), None, 0, False
    if clock:
        hour, minute, sure = _clock_time(clock)
    if day:
        word = day["day"].lower()
        part = next((name for name in PART_OF_DAY if name in word), None)
        if word.startswith("tomorrow"):
            target = base.date() + dt.timedelta(days=1)
        elif not word.startswith(("tonight", "this")):
            weekday = WEEKDAYS.index(word.split()[-1])
            ahead = (weekday - base.weekday()) % 7 or 7
            target = base.date() + dt.timedelta(days=ahead)
        if hour is None:
            hour = PART_OF_DAY[part] if part else 9
            sure = True
        elif part in ("afternoon", "evening", "night", "tonight") and hour < 12:
            hour, sure = hour + 12, True
    due = dt.datetime.combine(target, dt.time(hour, minute))
    if not day:
        if not sure and due <= base and hour < 12 and due + dt.timedelta(hours=12) > base:
            due += dt.timedelta(hours=12)  # "at 5" said at 2 PM means 5 PM
        elif due <= base:
            due += dt.timedelta(days=1)
    left = text
    for match in sorted(filter(None, (day, clock)), key=lambda m: m.start(), reverse=True):
        left = left[:match.start()] + " " + left[match.end():]
    return due.timestamp(), left


def repeat_from(text: str):
    # ({"days": [0..6], "at": "HH:MM"}, the text left) for "every weekday at 8", or (None, text).
    found = REPEAT.search(text)
    if not found:
        return None, text
    word = (found["every"] or found["daily"] or found["on"] or found["each"]).lower()
    if word in ("day", "daily", "morning", "evening", "night"):
        days = list(range(7))
    elif word.startswith("weekday"):
        days = list(range(5))
    elif word.startswith("weekend"):
        days = [5, 6]
    else:
        days = sorted({WEEKDAYS.index(name.rstrip("s")) for name in re.findall(_WEEKDAY, word)})
    left = text[:found.start()] + " " + text[found.end():]
    clock = CLOCK.search(left)
    hour, minute = REPEAT_TIMES.get(word, (9, 0))
    if clock:
        hour, minute, sure = _clock_time(clock)
        if not sure and word in ("evening", "night") and hour < 12:
            hour += 12
        left = left[:clock.start()] + " " + left[clock.end():]
    return {"days": days, "at": f"{hour:02d}:{minute:02d}"}, left


def next_time(repeat: dict, after: float) -> float:
    # The first moment after `after` that the repeat names.
    hour, minute = (int(part) for part in repeat["at"].split(":"))
    start = dt.datetime.fromtimestamp(after)
    for ahead in range(8):
        day = start.date() + dt.timedelta(days=ahead)
        moment = dt.datetime.combine(day, dt.time(hour, minute))
        if day.weekday() in repeat["days"] and moment.timestamp() > after:
            return moment.timestamp()
    raise ValueError("no days to repeat on")


def repeat_words(repeat: dict) -> str:
    days = sorted(repeat["days"])
    clock = dt.datetime.combine(dt.date.today(), dt.time(*(int(p) for p in repeat["at"].split(":")))).strftime("%I:%M %p").lstrip("0")
    if days == list(range(7)):
        every = "every day"
    elif days == list(range(5)):
        every = "every weekday"
    elif days == [5, 6]:
        every = "every weekend"
    else:
        names = [WEEKDAYS[d].capitalize() for d in days]
        every = "every " + (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0])
    return f"{every} at {clock}"


def task_words(text: str) -> str:
    # "to call dana" -> "Call dana".
    words = " ".join(text.split()).strip(" ,.!?;:")
    words = re.sub(r"^(?:me\s+)?(?:(?:to|that|about|of|for)\s+)+(?:i\s+(?:need|have|want)\s+to\s+)?", "", words, flags=re.IGNORECASE)
    words = re.sub(r"\s+(?:to|that|about)$", "", words, flags=re.IGNORECASE).strip(" ,.!?;:")
    return words[:1].upper() + words[1:]


class ReminderBook:
    def __init__(self, path):
        self.path = path  # a function giving reminders.json's path
        self.lock = threading.Lock()
        self.changed = threading.Event()  # set whenever reminders change, for the clock that fires them

    def _load(self) -> list:
        try:
            with open(self.path(), encoding="utf-8") as f:
                items = json.load(f)
        except (OSError, ValueError):
            return []
        return [r for r in items if isinstance(r, dict) and isinstance(r.get("due"), (int, float)) and r.get("text")] \
            if isinstance(items, list) else []

    def _save(self, items: list):
        path = self.path()
        with open(path + ".part", "w", encoding="utf-8") as f:
            json.dump(sorted(items, key=lambda r: r["due"]), f, ensure_ascii=False, indent=1)
        os.replace(path + ".part", path)
        self.changed.set()

    def upcoming(self) -> list:
        with self.lock:
            return sorted(self._load(), key=lambda r: r["due"])

    def add(self, text: str, due: float, kind: str = "reminder", repeat=None, **extra) -> dict:
        reminder = dict(extra, id=secrets.token_hex(4), text=str(text)[:300], due=float(due), kind=kind, made=time.time())
        if repeat:
            reminder["repeat"] = repeat
        with self.lock:
            self._save(self._load() + [reminder])
        return reminder

    def cancel(self, test) -> list:
        # Removes the reminders test(reminder) picks; returns them.
        with self.lock:
            items = self._load()
            gone = [r for r in items if test(r)]
            if gone:
                self._save([r for r in items if not test(r)])
        return gone

    def take_due(self, now: float) -> list:
        # The reminders due by now (oldest first), taken out of the book (a repeating one stays, due next
        # time); any missed by more than their late limit are left out.
        with self.lock:
            items = self._load()
            due = [r for r in items if r["due"] <= now]
            if due:
                kept = [r for r in items if r["due"] > now]
                kept += [dict(r, due=next_time(r["repeat"], now)) for r in due if r.get("repeat")]
                self._save(kept)
        return sorted((r for r in due if now - r["due"] <= LATE_LIMITS.get(r.get("kind"), LATE_LIMIT)),
                      key=lambda r: r["due"])

    def next_due(self):
        upcoming = self.upcoming()
        return upcoming[0]["due"] if upcoming else None


def handle(text: str, book: ReminderBook, now: float = None):
    # The reply when text is about reminders or timers, else None.
    now = time.time() if now is None else now
    said = LEAD.sub("", " ".join(str(text).split())).strip(" .!?")
    if not said:
        return None
    if LIST.fullmatch(said):
        upcoming = [r for r in book.upcoming() if r.get("kind") != "routine"]
        if not upcoming:
            return "You have no reminders. Say \"remind me in 20 minutes to stretch\" to set one."
        lines = [f"{repeat_words(r['repeat']) if r.get('repeat') else clock_words(r['due'], now)}: {r['text']}"
                 for r in upcoming[:8]]
        more = f"\n...and {len(upcoming) - 8} more." if len(upcoming) > 8 else ""
        return f"Your reminder{'s' if len(upcoming) > 1 else ''}:\n" + "\n".join(lines) + more
    cancel = CANCEL.fullmatch(said)
    if cancel:
        about = (cancel["about"] or "").lower().strip()
        timers = cancel["what"].lower().startswith("timer")
        gone = book.cancel(lambda r: r.get("kind") == ("timer" if timers else "reminder")
                           and (not about or about in r["text"].lower()))
        if not gone:
            return f"There's no {'timer' if timers else 'reminder'}{' like that' if about else ''} to cancel."
        if len(gone) == 1:
            return f"Okay, I cancelled the {'timer' if timers else 'reminder'}: {gone[0]['text']}"
        return f"Okay, I cancelled {len(gone)} {'timers' if timers else 'reminders'}."
    remind = REMIND.match(said)
    if remind:
        repeat, left = repeat_from(remind["rest"])
        if repeat:
            task = task_words(left) or "Your reminder"
            book.add(task, next_time(repeat, now), repeat=repeat)
            return f"Okay, I'll remind you {repeat_words(repeat)}: {task}"
        try:
            due, left = when_from(remind["rest"], now)
        except ValueError:
            return "That isn't a time I know. Try \"at 6:30 pm\" or \"in 20 minutes\"."
        if due is None and re.match(r"(?:what|how|who|where|why|which|when did|when was)\b", remind["rest"], re.IGNORECASE):
            return None  # "remind me what I did yesterday" is a question, not a reminder
        if due is None:
            return "When should I remind you? Say something like \"remind me in 20 minutes to stretch\" or \"at 6 pm to call Dana\"."
        task = task_words(left) or "Your reminder"
        book.add(task, due)
        soon = due - now < 3600 and not CLOCK.search(remind["rest"])
        when = f"in {duration_words(due - now)}" if soon else clock_words(due, now)
        return f"Okay, I'll remind you {'at ' if when[0].isdigit() else ''}{when}: {task}"
    timer = TIMER.fullmatch(said)
    if timer:
        length = DURATION.search("in " + (timer["a"] or timer["b"]).replace("-", " "))
        if not length:
            return None
        seconds = seconds_in(length)
        words = duration_words(seconds)
        label = words if " and " in words else re.sub(r"^(\d+) (\w+?)s?$", r"\1-\2", words)  # "5-minute"
        book.add(f"Your {label} timer is done", now + seconds, "timer")
        return f"Timer set for {words}."
    return None
