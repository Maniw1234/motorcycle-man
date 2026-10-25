"""Reading text off pictures of the screen with Windows' own text recognition (Windows.Media.Ocr), through a small
PowerShell script: nothing to install, and it reads in the languages Windows has for the user."""
import os
import subprocess
import tempfile

SCRIPT = r'''
param([string]$Path)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $null = $task.Wait(-1); $task.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { exit 3 }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$stream.Dispose()
foreach ($line in $result.Lines) { $line.Text }
'''
LARGEST = 4000  # pixels on a side; bigger pictures are made smaller first


class Unavailable(Exception):
    """Windows can't read text here (no language for it, or no PowerShell)."""


def read_text(picture, timeout: float = 30.0) -> str:
    """The text in a PIL picture, a line at a time. Small text is made bigger first, which Windows reads better."""
    if picture.height < 500 and picture.width < LARGEST // 2:
        picture = picture.resize((picture.width * 2, picture.height * 2))
    if max(picture.size) > LARGEST:
        picture = picture.copy()
        picture.thumbnail((LARGEST, LARGEST))
    folder = tempfile.mkdtemp(prefix="mm-ocr-")
    script, path = os.path.join(folder, "read.ps1"), os.path.join(folder, "screen.png")
    try:
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write(SCRIPT)
        picture.convert("RGB").save(path)
        try:
            done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, path],
                                  capture_output=True, timeout=timeout, creationflags=0x08000000)  # CREATE_NO_WINDOW
        except (OSError, subprocess.SubprocessError) as exc:
            raise Unavailable(str(exc)) from exc
        if done.returncode == 3:
            raise Unavailable("Windows has no language installed for reading text.")
        if done.returncode:
            raise Unavailable(done.stderr.decode("utf-8", "replace")[-300:])
        return "\n".join(line.strip() for line in done.stdout.decode("utf-8", "replace").splitlines() if line.strip())
    finally:
        for name in (script, path):
            try:
                os.remove(name)
            except OSError:
                pass
        try:
            os.rmdir(folder)
        except OSError:
            pass
