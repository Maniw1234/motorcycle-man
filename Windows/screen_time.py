"""Screen time: which app is in front while someone is at the PC, added up by the day (kept for two weeks)."""
import datetime as dt
import json
import threading
import time

KEEP_DAYS = 14


def day_of(stamp: float = None) -> str:
    return dt.date.fromtimestamp(time.time() if stamp is None else stamp).isoformat()


def span_words(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


class ScreenTime:
    def __init__(self, path):
        self._path = path  # a function giving screen_time.json's path
        self._lock = threading.Lock()
        self._days, self._paths = None, {}

    def _load(self):
        if self._days is None:
            try:
                with open(self._path(), encoding="utf-8") as f:
                    saved = json.load(f)
                self._days, self._paths = dict(saved.get("days", {})), dict(saved.get("paths", {}))
            except (OSError, ValueError, AttributeError):
                self._days, self._paths = {}, {}

    def add(self, app: str, seconds: float, path: str = "", stamp: float = None):
        with self._lock:
            self._load()
            today = self._days.setdefault(day_of(stamp), {})
            today[app] = today.get(app, 0) + seconds
            if path:
                self._paths[app] = path

    def save(self, now: float = None):
        with self._lock:
            self._load()
            oldest = (dt.date.fromtimestamp(time.time() if now is None else now) - dt.timedelta(days=KEEP_DAYS)).isoformat()
            self._days = {day: apps for day, apps in self._days.items() if day > oldest}
            try:
                with open(self._path(), "w", encoding="utf-8") as f:
                    json.dump({"days": self._days, "paths": self._paths}, f)
            except OSError:
                pass

    def day(self, day: str = None) -> list:
        """[(app, seconds)] for a day, the most used first."""
        with self._lock:
            self._load()
            return sorted(self._days.get(day or day_of(), {}).items(), key=lambda kv: -kv[1])

    def week(self, now: float = None) -> list:
        """[(day, seconds)] for the last seven days, oldest first."""
        today = dt.date.fromtimestamp(time.time() if now is None else now)
        with self._lock:
            self._load()
            return [((today - dt.timedelta(days=n)).isoformat(),
                     sum(self._days.get((today - dt.timedelta(days=n)).isoformat(), {}).values())) for n in range(6, -1, -1)]

    def path_of(self, app: str) -> str:
        with self._lock:
            self._load()
            return self._paths.get(app, "")


def words(apps: list) -> str:
    total = sum(seconds for _, seconds in apps)
    if total < 60:
        return "Hardly any screen time today yet."
    top = ", ".join(f"{app} {span_words(seconds)}" for app, seconds in apps[:3] if seconds >= 60)
    return f"{span_words(total)} on the PC today" + (f": {top}." if top else ".")
