"""What's playing on the PC, and controlling it: Windows' own media sessions (the ones its volume flyout shows, from
Spotify, browsers, the Media Player...), through a small PowerShell script, so nothing needs installing."""
import base64
import json
import os
import subprocess
import tempfile
import threading

SCRIPT = r'''
param([string]$Do = "info", [double]$At = -1)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $null = $task.Wait(5000); $task.Result }
$manager = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) `
    ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$session = $manager.GetCurrentSession()
if ($null -eq $session) { '{"title": null}'; exit }
switch ($Do) {
    "playpause" { $null = Await ($session.TryTogglePlayPauseAsync()) ([bool]) }
    "next" { $null = Await ($session.TrySkipNextAsync()) ([bool]) }
    "prev" { $null = Await ($session.TrySkipPreviousAsync()) ([bool]) }
    "seek" { $null = Await ($session.TryChangePlaybackPositionAsync([long]($At * 10000000))) ([bool]) }
}
if ($Do -ne "info") { Start-Sleep -Milliseconds 350 }
$props = Await ($session.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
$timeline = $session.GetTimelineProperties()
$playback = $session.GetPlaybackInfo()
$art = ""
if ($props.Thumbnail) {
    try {
        $stream = Await ($props.Thumbnail.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
        $reader = [Windows.Storage.Streams.DataReader]::new($stream)
        $size = [uint32]$stream.Size
        $null = Await ($reader.LoadAsync($size)) ([uint32])
        $bytes = New-Object byte[] $size
        $reader.ReadBytes($bytes)
        $art = [Convert]::ToBase64String($bytes)
        $stream.Dispose()
    } catch { $art = "" }
}
$end = $timeline.EndTime.TotalSeconds
$position = $timeline.Position.TotalSeconds
if ($playback.PlaybackStatus -eq 'Playing' -and $end -gt 0) {
    $position += ([DateTimeOffset]::Now - $timeline.LastUpdatedTime).TotalSeconds  # where it is now, not at the last update
}
@{ title = $props.Title; artist = $props.Artist; album = $props.AlbumTitle; app = $session.SourceAppUserModelId;
   playing = ($playback.PlaybackStatus -eq 'Playing'); position = [math]::Round([math]::Min($position, [math]::Max($end, 0)), 1);
   length = [math]::Round($end, 1); can_seek = [bool]$playback.Controls.IsPlaybackPositionEnabled; art = $art } | ConvertTo-Json -Compress
'''
FRIENDLY_APPS = {"spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "zunemusic": "Media Player",
                 "microsoft.zunemusic": "Media Player", "vlc": "VLC", "itunes": "iTunes", "applemusic": "Apple Music",
                 "youtube": "YouTube", "music.youtube": "YouTube Music", "deezer": "Deezer", "tidal": "Tidal", "amazonmusic": "Amazon Music"}
_SCRIPT_PATH = {"path": None}
_LOCK = threading.Lock()


class Unavailable(Exception):
    """Windows won't say what's playing here."""


def app_label(app_id: str) -> str:
    # "Spotify.exe", "Chrome", "Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic" -> a name to show.
    low = (app_id or "").lower()
    for key, name in FRIENDLY_APPS.items():
        if key in low:
            return name
    stem = os.path.splitext(os.path.basename((app_id or "").split("!")[-1]))[0]
    return stem[:1].upper() + stem[1:] if stem else "The PC"


def _script() -> str:
    with _LOCK:
        if not _SCRIPT_PATH["path"] or not os.path.exists(_SCRIPT_PATH["path"]):
            path = os.path.join(tempfile.mkdtemp(prefix="mm-media-"), "media.ps1")
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(SCRIPT)
            _SCRIPT_PATH["path"] = path
        return _SCRIPT_PATH["path"]


def media(do: str = "info", at: float = -1.0, timeout: float = 20.0) -> dict:
    """{"title", "artist", "album", "app", "playing", "position", "length", "can_seek", "art": PNG/JPEG bytes or b""},
    after doing do ("playpause", "next", "prev", "seek" to at seconds); {"title": None} when nothing plays."""
    if do not in ("info", "playpause", "next", "prev", "seek"):
        raise ValueError(do)
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", _script(),
                               "-Do", do, "-At", str(float(at))], capture_output=True, timeout=timeout, creationflags=0x08000000)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unavailable(str(exc)) from exc
    try:
        found = json.loads(done.stdout.decode("utf-8", "replace").strip() or "{}")
    except ValueError as exc:
        raise Unavailable(done.stderr.decode("utf-8", "replace")[-300:]) from exc
    if not isinstance(found, dict) or not found.get("title"):
        return {"title": None}
    found["art"] = base64.b64decode(found.get("art") or "")
    found["app"] = app_label(found.get("app", ""))
    return found
