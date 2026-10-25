# Brings Motorcycle Man back when it crashes. The built app starts this small watcher beside itself (the same
# exe with --keep-running <its process id>; motorcycle_man.py hands over before loading anything else), and the
# watcher waits for it to end. A crash (one of Windows' exception codes, such as an access violation) starts
# Motorcycle Man again by the clock; Quit, Task Manager and Windows shutting down don't. At most 3 restarts in
# 10 minutes, so a crash at every start can't go round and round. In October 2026 the phone kept losing the PC,
# and nothing showed whether Motorcycle Man had crashed.
import ctypes
import ctypes.wintypes
import json
import os
import subprocess
import sys
import time

CRASHED_FROM = 0xC0000000  # Windows' exception codes: 0xC0000005 access violation, 0xC00000FD stack overflow...
MOST_RESTARTS, RESTART_WINDOW = 3, 600
SYNCHRONIZE, PROCESS_QUERY_LIMITED_INFORMATION = 0x00100000, 0x1000
INFINITE = 0xFFFFFFFF
SM_SHUTTINGDOWN = 0x2000
CREATE_NO_WINDOW, CREATE_BREAKAWAY_FROM_JOB = 0x08000000, 0x01000000


def crashed(exit_code: int) -> bool:
    return exit_code & 0xFFFFFFFF >= CRASHED_FROM


def may_restart(history: list, now: float) -> bool:
    return len([t for t in history if now - t < RESTART_WINDOW]) < MOST_RESTARTS


def fresh_environment(env=None) -> dict:
    # Without the notes PyInstaller leaves for its own copies, the exe unpacks itself as an app of its own
    # instead of borrowing the folder of the copy that started it (which goes away when that copy ends).
    env = dict(os.environ if env is None else env)
    for name in list(env):
        if name.upper().startswith(("_PYI_", "_MEIPASS")):
            del env[name]
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def start_detached(command: list, env: dict):
    # Started apart from the caller, so it outlives it (out of the caller's job, when the job allows that).
    try:
        return subprocess.Popen(command, env=env, close_fds=True, creationflags=CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        return subprocess.Popen(command, env=env, close_fds=True, creationflags=CREATE_NO_WINDOW)


def app_data_dir() -> str:
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Motorcycle Man")


def note(text: str):
    # A line in Motorcycle Man's own log (only written after Motorcycle Man has ended, so they never write at once).
    try:
        with open(os.path.join(app_data_dir(), "motorcycle_man.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S,000 WARNING ") + text + "\n")
    except OSError:
        pass


def after_exit(exit_code: int, now=None, shutting_down=None, start=start_detached) -> bool:
    # What follows Motorcycle Man ending with exit_code; True when it was started again.
    now = time.time() if now is None else now
    if not crashed(exit_code):
        return False
    if (shutting_down or (lambda: bool(ctypes.windll.user32.GetSystemMetrics(SM_SHUTTINGDOWN))))():
        return False
    note(f"Motorcycle Man crashed (0x{exit_code & 0xFFFFFFFF:08X})")
    path = os.path.join(app_data_dir(), "restarts.json")
    try:
        with open(path, encoding="utf-8") as f:
            history = [t for t in json.load(f) if isinstance(t, (int, float))]
    except (OSError, ValueError, TypeError):
        history = []
    if not may_restart(history, now):
        note(f"Not starting it again: it crashed {MOST_RESTARTS} times in {RESTART_WINDOW // 60} minutes")
        return False
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump([t for t in history if now - t < RESTART_WINDOW] + [now], f)
    except OSError:
        pass
    start([sys.executable, "--tray", "--restarted"], fresh_environment())
    note("Started Motorcycle Man again")
    return True


def main(argv: list) -> int:
    try:
        pid = int(argv[argv.index("--keep-running") + 1])
    except (ValueError, IndexError):
        return 2
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD)
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(ctypes.wintypes.DWORD))
    handle = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 1
    kernel32.WaitForSingleObject(handle, INFINITE)
    code = ctypes.wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
        return 1
    after_exit(code.value)
    return 0
