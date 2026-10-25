# Motorcycle Man updates itself from its GitHub Releases. Each new version is tagged ("v1.0", "v1.1"...), and GitHub
# builds Motorcycle Man.exe and the Android app for it (.github/workflows/release.yml) and publishes them as a
# release with SHA256SUMS.txt. The app compares that version with its own (version.json, written by the same build;
# a copy made on a PC has none and counts as older than any release), offers the update, and installs it only when
# the user says so: the new exe is downloaded, checked against SHA256SUMS.txt, and started with --finish-update,
# which waits for this one to close, puts itself in its place and starts it again.
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.request

import keep_running

UPDATE_REPO = "Maniw1234/motorcycle-man"  # a fork changes this to its own repository
# Release file names have no spaces: GitHub turns them into dots ("Motorcycle.Man.exe").
EXE_ASSET, SUMS_ASSET = "MotorcycleMan.exe", "SHA256SUMS.txt"
DOWNLOAD_LIMIT = 400 << 20  # bytes: the exe is about 70 MB, so a far bigger download is stopped
USER_AGENT = "MotorcycleMan-updater"


class UpdateError(RuntimeError):
    pass


VERSION_TAG = re.compile(r"v?(\d{1,4}(?:\.\d{1,4}){1,2})")


def own_version(path: str) -> str:
    # This copy's version ("1.0"), from version.json beside it (bundled into the exe by the GitHub build), or ""
    # for a copy made on a PC.
    try:
        with open(path, encoding="utf-8") as f:
            version = str(json.load(f).get("version", ""))
    except (OSError, ValueError, TypeError, AttributeError):
        return ""
    return version if VERSION_TAG.fullmatch(version) else ""


def version_key(version: str) -> tuple:
    # "1.10" > "1.9" > "1.0" = "1.0.0" > "" (a copy made on a PC).
    match = VERSION_TAG.fullmatch(str(version))
    if not match:
        return (-1,)
    parts = [int(n) for n in match.group(1).split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def latest_release(repo: str = UPDATE_REPO, opener=None, timeout: float = 15) -> dict:
    # {"version", "tag", "exe_url", "sums_url", "size", "notes"} for the newest release, or {} when there is none
    # usable (no release yet, no exe in it, or GitHub can't be reached).
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as reply:
            data = json.loads(reply.read(1 << 20).decode("utf-8"))
    except (OSError, ValueError):
        return {}
    tag = str(data.get("tag_name", ""))
    match = VERSION_TAG.fullmatch(tag)
    if not match or data.get("draft") or data.get("prerelease"):
        return {}
    assets = {str(a.get("name", "")): a for a in data.get("assets") or [] if isinstance(a, dict)}
    exe, sums = assets.get(EXE_ASSET), assets.get(SUMS_ASSET)
    if not exe or not sums:
        return {}
    return {"version": match.group(1), "tag": tag, "exe_url": str(exe.get("browser_download_url", "")),
            "sums_url": str(sums.get("browser_download_url", "")), "size": int(exe.get("size") or 0),
            "notes": str(data.get("body") or "")[:2000]}


def newer(release: dict, version: str) -> bool:
    return bool(release) and version_key(release.get("version", "")) > version_key(version)


def _github_file(url: str) -> bool:
    # Release files come from GitHub only (the API names them; redirects go to its download servers).
    return url.startswith(("https://github.com/", "https://objects.githubusercontent.com/"))


def download_update(release: dict, folder: str, opener=None, progress=lambda done, total: None) -> str:
    # Downloads the release's exe into folder and checks it against its SHA256SUMS.txt; returns its path.
    if not (_github_file(release.get("exe_url", "")) and _github_file(release.get("sums_url", ""))):
        raise UpdateError("The update isn't on GitHub.")
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(urllib.request.Request(release["sums_url"], headers={"User-Agent": USER_AGENT}), timeout=30) as reply:
            sums = reply.read(64 << 10).decode("utf-8", "replace")
    except OSError as e:
        raise UpdateError(f"Couldn't get the update's checksums: {e}") from None
    expected = next((line.split()[0].lower() for line in sums.splitlines()
                     if line.strip().endswith(EXE_ASSET) and len(line.split()[0]) == 64), "")
    if not expected:
        raise UpdateError(f"The update has no checksum for {EXE_ASSET}.")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"Motorcycle Man {release['version']}.exe")
    partial, digest, done = path + ".part", hashlib.sha256(), 0
    try:
        with open_url(urllib.request.Request(release["exe_url"], headers={"User-Agent": USER_AGENT}), timeout=60) as reply, \
                open(partial, "wb") as out:
            while True:
                chunk = reply.read(1 << 20)
                if not chunk:
                    break
                done += len(chunk)
                if done > DOWNLOAD_LIMIT:
                    raise UpdateError("The update is far bigger than it should be.")
                digest.update(chunk)
                out.write(chunk)
                progress(done, release.get("size", 0))
        if digest.hexdigest() != expected:
            raise UpdateError("The downloaded update didn't match its checksum, so it wasn't used.")
        os.replace(partial, path)
        return path
    except OSError as e:
        raise UpdateError(f"Couldn't download the update: {e}") from None
    finally:
        if os.path.exists(partial):
            os.remove(partial)


def start_install(new_exe: str, current_exe: str, pid: int, env: dict = None):
    # Starts the new exe, which waits for this Motorcycle Man (pid) to close and then takes its place.
    return keep_running.start_detached([new_exe, "--finish-update", current_exe, str(int(pid))],
                                       keep_running.fresh_environment(env))


def start_updated(path: str):
    # The new version, in the tray like the one it replaced, as an app of its own (not borrowing the files of the
    # helper that started it, which go away when the helper ends).
    return keep_running.start_detached([path, "--tray", "--updated"], keep_running.fresh_environment())


def wait_for_exit(pid: int, seconds: float) -> bool:
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    handle = kernel32.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
    if not handle:
        return True  # already gone
    try:
        return kernel32.WaitForSingleObject(handle, int(seconds * 1000)) == 0
    finally:
        kernel32.CloseHandle(handle)


def finish_update(argv: list, source: str = None, wait=wait_for_exit, launch=None) -> int:
    # Run by the new exe (motorcycle_man.py hands over before loading anything else): once the old Motorcycle Man
    # has closed, the old exe is kept beside it as "<name>.old" and this one (source) is copied into its place
    # and started there.
    try:
        at = argv.index("--finish-update")
        current, pid = argv[at + 1], int(argv[at + 2])
    except (ValueError, IndexError):
        return 2
    source = source or sys.executable
    if not wait(pid, 60):
        return 3  # the old one didn't close: nothing is touched
    backup = current + ".old"
    for _ in range(20):  # Windows may hold the old file for a moment after its process ends
        try:
            if os.path.exists(backup):
                os.remove(backup)
            if os.path.exists(current):
                os.replace(current, backup)
            shutil.copy2(source, current)
            break
        except OSError:
            time.sleep(0.5)
    else:
        if os.path.exists(backup) and not os.path.exists(current):
            os.replace(backup, current)  # put the old one back rather than leave nothing
        return 4
    (launch or start_updated)(current)
    return 0


def clean_up(current_exe: str, folder: str):
    # After an update: the old exe kept beside the new one, and the downloaded files.
    leftovers = [current_exe + ".old"]
    if os.path.isdir(folder):
        leftovers += [os.path.join(folder, name) for name in os.listdir(folder)
                      if name.startswith("Motorcycle Man ") and name.endswith((".exe", ".part"))]
    for path in leftovers:
        try:
            os.remove(path)
        except OSError:
            pass
