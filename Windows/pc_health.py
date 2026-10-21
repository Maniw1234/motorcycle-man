"""How the PC is doing, and telling the user when something needs them or is done.

readings(): the battery, CPU, memory, system disk and how long the PC has been on, read straight from Windows.
busiest(): the apps using the most CPU and memory (an app's processes counted together, like Chrome's).
HealthWatch: the battery running low (20% and 10% while unplugged), the system disk nearly full, and
    downloads finishing (new files in Downloads, once they stop growing).
FinishWatch: "tell me when this finishes": a download still coming in, or else the window in front, which
    is done when it closes, says it's done, or its app goes quiet after working.
"""
import ctypes
import ctypes.wintypes
import os
import re
import shutil
import threading
import time

DWORD, ULL = ctypes.wintypes.DWORD, ctypes.c_ulonglong
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32")


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", DWORD), ("BatteryFullLifeTime", DWORD)]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", DWORD), ("dwMemoryLoad", DWORD), ("ullTotalPhys", ULL), ("ullAvailPhys", ULL),
                ("ullTotalPageFile", ULL), ("ullAvailPageFile", ULL), ("ullTotalVirtual", ULL), ("ullAvailVirtual", ULL),
                ("ullAvailExtendedVirtual", ULL)]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [("cb", DWORD), ("PageFaultCount", DWORD)] + [(name, ctypes.c_size_t) for name in (
        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


_kernel32.GetTickCount64.restype = ULL
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
_kernel32.OpenProcess.argtypes = (DWORD, ctypes.wintypes.BOOL, DWORD)
_kernel32.CloseHandle.argtypes = (ctypes.wintypes.HANDLE,)
_kernel32.GetProcessTimes.argtypes = (ctypes.wintypes.HANDLE,) + (ctypes.POINTER(ctypes.wintypes.FILETIME),) * 4
_kernel32.QueryFullProcessImageNameW.argtypes = (ctypes.wintypes.HANDLE, DWORD, ctypes.wintypes.LPWSTR, ctypes.POINTER(DWORD))
_kernel32.K32GetProcessMemoryInfo.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX), DWORD)
_user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = (ctypes.wintypes.HWND, ctypes.POINTER(DWORD))
_user32.IsWindow.argtypes = (ctypes.wintypes.HWND,)
_user32.GetWindowTextW.argtypes = (ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int)
QUERY_LIMITED = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION: enough for times, memory and the program's path
CORES = os.cpu_count() or 1
GB = 1 << 30


def _ticks(filetime) -> int:
    return (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime  # in 100 ns


def battery():
    """{"percent", "charging", "minutes_left"}, or None on a PC without a battery."""
    status = SYSTEM_POWER_STATUS()
    if not _kernel32.GetSystemPowerStatus(ctypes.byref(status)) or status.BatteryFlag & 128 or status.BatteryLifePercent > 100:
        return None
    left = status.BatteryLifeTime
    return {"percent": status.BatteryLifePercent, "charging": status.ACLineStatus == 1,
            "minutes_left": None if left == 0xFFFFFFFF or status.ACLineStatus == 1 else left // 60}


class CpuMeter:
    """How busy the CPU has been since it was last asked (or over a quarter of a second, the first time)."""

    def __init__(self):
        self._last, self._lock = None, threading.Lock()

    @staticmethod
    def _times():
        idle, kernel, user = ctypes.wintypes.FILETIME(), ctypes.wintypes.FILETIME(), ctypes.wintypes.FILETIME()
        _kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        return time.monotonic(), _ticks(idle), _ticks(kernel) + _ticks(user)  # kernel time includes idle time

    def percent(self) -> int:
        with self._lock:
            last = self._last
            if last is None or time.monotonic() - last[0] < 0.2:
                last = self._times()
                time.sleep(0.25)
            now = self._last = self._times()
        busy = now[2] - last[2]
        return max(0, min(100, round(100 * (1 - (now[1] - last[1]) / busy)))) if busy > 0 else 0


CPU = CpuMeter()


def memory() -> dict:
    status = MEMORYSTATUSEX(dwLength=ctypes.sizeof(MEMORYSTATUSEX))
    _kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return {"percent": status.dwMemoryLoad, "used_gb": round((status.ullTotalPhys - status.ullAvailPhys) / GB, 1),
            "total_gb": round(status.ullTotalPhys / GB, 1)}


def disk(path: str = None) -> dict:
    path = path or os.environ.get("SystemDrive", "C:") + "\\"
    usage = shutil.disk_usage(path)
    return {"drive": path[:2], "free_gb": round(usage.free / GB, 1), "total_gb": round(usage.total / GB),
            "percent": round(100 * usage.used / usage.total) if usage.total else 0}


def readings() -> dict:
    return {"battery": battery(), "cpu": CPU.percent(), "memory": memory(), "disk": disk(),
            "uptime": _kernel32.GetTickCount64() // 1000}


def span_words(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{hours // 24} days"


def battery_words(power) -> str:
    if not power:
        return "This PC has no battery."
    if power["charging"]:
        return f"Battery {power['percent']}%, charging." if power["percent"] < 100 else "Battery full, plugged in."
    left = f", about {span_words(power['minutes_left'] * 60)} left" if power["minutes_left"] else ""
    return f"Battery {power['percent']}%{left}."


def words(now: dict) -> str:
    parts = [battery_words(now["battery"])] if now["battery"] else []
    parts += [f"CPU {now['cpu']}%.", f"Memory {now['memory']['percent']}% of {now['memory']['total_gb']:g} GB.",
              f"{now['disk']['drive']} has {now['disk']['free_gb']:g} GB free of {now['disk']['total_gb']} GB.",
              f"On for {span_words(now['uptime'])}."]
    return " ".join(parts)


# --- the busiest apps ---

FRIENDLY = {"msedge": "Edge", "chrome": "Chrome", "firefox": "Firefox", "code": "VS Code", "explorer": "File Explorer",
            "msmpeng": "Windows Security", "svchost": "Windows services", "dwm": "Windows' screen drawing",
            "searchhost": "Windows Search", "teams": "Teams", "ms-teams": "Teams", "winword": "Word", "excel": "Excel",
            "powerpnt": "PowerPoint", "outlook": "Outlook", "olk": "Outlook", "discord": "Discord", "spotify": "Spotify",
            "steam": "Steam", "steamwebhelper": "Steam", "whatsapp": "WhatsApp", "obs64": "OBS", "vlc": "VLC",
            "memory compression": "Windows memory compression", "system": "Windows", "python": "Python",
            "motorcycle man": "Motorcycle Man", "msedgewebview2": "Apps' web views", "claude": "Claude",
            "textinputhost": "Windows keyboard", "runtimebroker": "Windows apps' helper"}


def app_name(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    return FRIENDLY.get(stem.lower(), stem[:1].upper() + stem[1:])


def processes() -> dict:
    """pid -> (app name, CPU time so far in 100 ns, private memory in bytes), for each process Windows shows us."""
    ids = (DWORD * 4096)()
    needed = DWORD()
    if not _kernel32.K32EnumProcesses(ids, ctypes.sizeof(ids), ctypes.byref(needed)):
        return {}
    found = {}
    for pid in ids[:needed.value // ctypes.sizeof(DWORD)]:
        handle = _kernel32.OpenProcess(QUERY_LIMITED, False, pid) if pid else None
        if not handle:
            continue
        try:
            path, size = ctypes.create_unicode_buffer(520), DWORD(520)
            if not _kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                continue
            made, ended, kernel, user = (ctypes.wintypes.FILETIME() for _ in range(4))
            _kernel32.GetProcessTimes(handle, ctypes.byref(made), ctypes.byref(ended), ctypes.byref(kernel), ctypes.byref(user))
            counters = PROCESS_MEMORY_COUNTERS_EX(cb=ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX))
            _kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            found[pid] = (app_name(path.value), _ticks(kernel) + _ticks(user), counters.PrivateUsage)
        finally:
            _kernel32.CloseHandle(handle)
    return found


def busiest(seconds: float = 0.6, top: int = 5) -> dict:
    """{"cpu": [(app, percent of the whole CPU)], "memory": [(app, GB)]}, busiest first."""
    before = processes()
    time.sleep(seconds)
    after = processes()
    cpu, memory_use = {}, {}
    for pid, (name, ticks, private) in after.items():
        if pid in before and before[pid][0] == name:
            cpu[name] = cpu.get(name, 0) + max(0, ticks - before[pid][1])
        memory_use[name] = memory_use.get(name, 0) + private
    whole = seconds * 1e7 * CORES
    return {"cpu": [(name, round(100 * t / whole, 1)) for name, t in sorted(cpu.items(), key=lambda kv: -kv[1])[:top]
                    if round(100 * t / whole, 1) >= 0.5],
            "memory": [(name, round(b / GB, 1)) for name, b in sorted(memory_use.items(), key=lambda kv: -kv[1])[:top]]}


def busiest_words(busy: dict) -> str:
    cpu = ", ".join(f"{name} {share:g}%" for name, share in busy["cpu"][:3]) or "nothing much"
    mem = ", ".join(f"{name} {gb:g} GB" for name, gb in busy["memory"][:3])
    return f"Using the CPU: {cpu}. Using memory: {mem}."


# --- alerts ---

PARTIAL = (".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload", ".!ut", ".aria2")
LOW_BATTERY = (20, 10)
DISK_LOW_GB, DISK_LOW_PERCENT = 5.0, 95
SWITCHES = {"low_battery": "Low battery", "downloads": "Finished downloads", "disk": "Disk almost full"}


def is_partial(name: str) -> bool:
    low = name.lower()
    return low.endswith(PARTIAL) or low.startswith(("~$", ".")) or low in ("desktop.ini", "thumbs.db")


def size_words(size: int) -> str:
    for unit, scale in (("GB", GB), ("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}".replace(".0 ", " ")
    return f"{size} bytes"


class HealthWatch:
    """Checks now and then; alert(kind, text) tells the user, for the kinds switched on (switches() -> dict)."""
    BATTERY_EVERY, DOWNLOADS_EVERY = 60.0, 3.0

    def __init__(self, alert, switches, downloads_folder):
        self._alert, self._switches, self._folder = alert, switches, downloads_folder
        self._battery_warned = 101  # the lowest level already warned about while unplugged
        self._disk_warned_at = -1e9
        self._known, self._growing = None, {}

    def _on(self, kind) -> bool:
        return self._switches().get(kind, True)

    def check_battery(self, power):
        if not power or power["charging"]:
            self._battery_warned = 101
            return
        for level in LOW_BATTERY:
            if power["percent"] <= level < self._battery_warned:
                self._battery_warned = level
                if self._on("low_battery"):
                    left = f" (about {span_words(power['minutes_left'] * 60)} left)" if power["minutes_left"] else ""
                    self._alert("low_battery", f"🔋 Battery at {power['percent']}%{left}: plug in the charger.")
                return

    def check_disk(self, space, now: float = None):
        now = time.time() if now is None else now
        if (space["free_gb"] < DISK_LOW_GB or space["percent"] >= DISK_LOW_PERCENT) and now - self._disk_warned_at > 86400:
            self._disk_warned_at = now
            if self._on("disk"):
                self._alert("disk", f"💾 {space['drive']} is almost full: {space['free_gb']:g} GB left. Emptying the Recycle "
                                    "Bin or Downloads frees some.")

    def check_downloads(self):
        try:
            here = {e.name: e.stat().st_size for e in os.scandir(self._folder) if e.is_file()}
        except OSError:
            return
        if self._known is None:
            self._known = set(here)
            return
        self._known &= set(here)  # a file deleted and downloaded again is news again
        coming = {name[:-len(ext)] for name in here for ext in PARTIAL if name.lower().endswith(ext)}
        for name, size in here.items():
            if name in self._known or is_partial(name) or name in coming:
                continue
            if size and self._growing.get(name) == size:  # stopped growing: finished
                self._known.add(name)
                self._growing.pop(name, None)
                if self._on("downloads"):
                    self._alert("downloads", f"⬇ Downloaded “{name}” ({size_words(size)}).")
            else:
                self._growing[name] = size
        self._growing = {name: size for name, size in self._growing.items() if name in here}

    def run(self, stopped: threading.Event):
        next_check = 0.0
        while not stopped.is_set():
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + self.BATTERY_EVERY
                try:
                    self.check_battery(battery())
                    self.check_disk(disk())
                except OSError:
                    pass
            self.check_downloads()
            stopped.wait(self.DOWNLOADS_EVERY)


# --- "tell me when this finishes" ---

DONE_WORDS = re.compile(r"\b(?:complete|completed|finished|done|succeeded|successful|ready)\b", re.IGNORECASE)


def window_title(hwnd) -> str:
    title = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(hwnd, title, 256)
    return title.value


def window_process(hwnd) -> int:
    pid = DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def process_ticks(pid: int):
    handle = _kernel32.OpenProcess(QUERY_LIMITED, False, pid)
    if not handle:
        return None
    try:
        made, ended, kernel, user = (ctypes.wintypes.FILETIME() for _ in range(4))
        if not _kernel32.GetProcessTimes(handle, ctypes.byref(made), ctypes.byref(ended), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return _ticks(kernel) + _ticks(user)
    finally:
        _kernel32.CloseHandle(handle)


class FinishWatch:
    """One "tell me when it's done" at a time. done(text) tells the user."""
    EVERY = 2.0
    BUSY, QUIET = 8.0, 1.5  # percent of one core: working, then quiet
    QUIET_FOR = 30.0  # seconds of quiet after working that mean it's done
    LONGEST = 6 * 3600

    def __init__(self, done, downloads_folder):
        self._done, self._folder = done, downloads_folder
        self._lock = threading.Lock()
        self._watching = None  # what it watches: a dict, replaced by each start

    def what(self) -> str:
        with self._lock:
            return self._watching["name"] if self._watching else ""

    def stop(self) -> str:
        with self._lock:
            was, self._watching = self._watching, None
        return f"Okay, I stopped watching {was['name']}." if was else "I'm not watching anything."

    def start(self, foreground=None) -> str:
        try:
            coming = sorted(n for n in os.listdir(self._folder) if n.lower().endswith(PARTIAL))
        except OSError:
            coming = []
        if coming:
            job = {"kind": "download", "files": set(coming), "name": "your download" if len(coming) == 1 else "your downloads"}
        else:
            hwnd = _user32.GetForegroundWindow() if foreground is None else foreground
            pid = window_process(hwnd) if hwnd else 0
            if not hwnd or not pid or pid == os.getpid():
                return "Bring the window you're waiting on to the front on the PC, then ask again."
            title = window_title(hwnd).strip() or "that window"
            job = {"kind": "window", "hwnd": hwnd, "pid": pid, "title": title, "name": f"“{title[:60]}”",
                   "ticks": process_ticks(pid), "worked": False, "quiet_since": None}
        job["started"] = time.monotonic()
        with self._lock:
            self._watching = job
        threading.Thread(target=self._watch, args=(job,), daemon=True).start()
        return f"Okay, I'll tell you when {job['name']} finishes."

    def _finish(self, job, text):
        with self._lock:
            if self._watching is not job:
                return
            self._watching = None
        self._done(text)

    def step(self, job, now: float = None) -> bool:
        """Looks once; True when it's over (and said so)."""
        now = time.monotonic() if now is None else now
        if now - job["started"] > self.LONGEST:
            self._finish(job, f"I stopped watching {job['name']} after 6 hours.")
            return True
        if job["kind"] == "download":
            try:
                left = job["files"] & set(os.listdir(self._folder))
            except OSError:
                left = set()
            if not left:
                self._finish(job, f"✅ {job['name'][0].upper()}{job['name'][1:]} finished.")
                return True
            return False
        if not _user32.IsWindow(job["hwnd"]):
            self._finish(job, f"✅ {job['name']} closed: it's finished.")
            return True
        title = window_title(job["hwnd"])
        if title != job["title"] and DONE_WORDS.search(title):
            self._finish(job, f"✅ {job['name']} says “{title[:60]}”.")
            return True
        ticks = process_ticks(job["pid"])
        if ticks is not None and job["ticks"] is not None:
            share = 100 * (ticks - job["ticks"]) / (self.EVERY * 1e7)
            if share >= self.BUSY:
                job["worked"], job["quiet_since"] = True, None
            elif share <= self.QUIET and job["worked"]:
                job["quiet_since"] = job["quiet_since"] or now
                if now - job["quiet_since"] >= self.QUIET_FOR:
                    self._finish(job, f"✅ {job['name']} looks finished: its app went quiet.")
                    return True
        job["ticks"] = ticks
        return False

    def _watch(self, job):
        while True:
            time.sleep(self.EVERY)
            with self._lock:
                if self._watching is not job:
                    return
            if self.step(job):
                return
