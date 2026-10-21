"""Quick PC settings for the phone: dark mode, a warm screen for the night, Wi-Fi, Bluetooth and Do not disturb.

Dark mode is Windows' own setting (in the registry, then announced so open apps follow). The warm screen is a
colour tint Motorcycle Man sets on the main screen (Windows' own Night light has no switch apps can use); it goes
when switched off, or when the PC restarts. Wi-Fi and Bluetooth are switched through Windows' radios API, and Do
not disturb by pressing its button in Windows' notification panel, as a person would.
"""
import ctypes
import ctypes.wintypes
import os
import subprocess
import tempfile
import time
import winreg

PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
WARM = (1.0, 0.9, 0.78)  # red, green and blue, of what they were
STATE = {"warm": False, "dnd": None}

RADIO_SCRIPT = r'''
param([string]$Kind = "", [int]$On = -1)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $null = $task.Wait(8000); $task.Result }
$null = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
foreach ($radio in $radios) {
    if ($Kind -ne "" -and "$($radio.Kind)" -eq $Kind -and $On -ge 0) {
        $state = if ($On -eq 1) { [Windows.Devices.Radios.RadioState]::On } else { [Windows.Devices.Radios.RadioState]::Off }
        $null = Await ($radio.SetStateAsync($state)) ([Windows.Devices.Radios.RadioAccessStatus])
        Start-Sleep -Milliseconds 400
    }
}
foreach ($radio in $radios) { "$($radio.Kind)=$($radio.State)" }
'''


class Unavailable(Exception):
    """That setting can't be changed on this PC."""


# --- dark mode ---

def dark_mode() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def set_dark_mode(on: bool):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE, 0, winreg.KEY_SET_VALUE) as key:
        for name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0 if on else 1)
    result = ctypes.c_size_t()
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 1000, ctypes.byref(result))  # WM_SETTINGCHANGE


# --- the warm screen ---

def set_warm_screen(on: bool) -> bool:
    ramp = (ctypes.wintypes.WORD * 768)()
    for i in range(256):
        for colour, share in enumerate(WARM if on else (1.0, 1.0, 1.0)):
            ramp[colour * 256 + i] = min(65535, int(i * 257 * share))
    screen = ctypes.windll.user32.GetDC(None)
    try:
        ok = bool(ctypes.windll.gdi32.SetDeviceGammaRamp(screen, ctypes.byref(ramp)))
    finally:
        ctypes.windll.user32.ReleaseDC(None, screen)
    if ok:
        STATE["warm"] = on
    return ok


# --- Wi-Fi and Bluetooth ---

def _radio_script() -> str:
    path = os.path.join(tempfile.gettempdir(), "motorcycle-man-radios.ps1")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(RADIO_SCRIPT)
    return path


def radios(kind: str = "", on: int = -1) -> dict:
    """{"wifi": True/False, "bluetooth": ...} for the radios this PC has, after switching kind ("WiFi" or
    "Bluetooth") on (1) or off (0) when asked."""
    try:
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", _radio_script(),
                               "-Kind", kind, "-On", str(int(on))], capture_output=True, timeout=30, creationflags=0x08000000)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unavailable(str(exc)) from exc
    found = {}
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        name, _, state = line.strip().partition("=")
        if name in ("WiFi", "Bluetooth"):
            found[name.lower()] = state == "On"
    return found


# --- Do not disturb ---

def _uia():
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import comtypes.client
    try:
        from comtypes.gen import UIAutomationClient as uia
    except ImportError:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia
    return uia, comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)


def set_do_not_disturb(on: bool, press_keys, wait: float = 3.0) -> bool:
    """Opens Windows' notification panel (press_keys(["win", "n"])), sets its Do not disturb button, and closes it.
    Returns the button's state after, or raises Unavailable when there's no such button."""
    uia, automation = _uia()
    press_keys(["win", "n"])
    try:
        button, end = None, time.monotonic() + wait
        named = automation.CreatePropertyCondition(uia.UIA_NamePropertyId, "Do not disturb")
        while button is None and time.monotonic() < end:
            time.sleep(0.25)
            panel = automation.GetRootElement().FindFirst(
                uia.TreeScope_Children, automation.CreatePropertyCondition(uia.UIA_NamePropertyId, "Notification Center"))
            button = panel.FindFirst(uia.TreeScope_Descendants, named) if panel else None
        if button is None:
            raise Unavailable("Windows' notification panel has no Do not disturb button here.")
        toggle = button.GetCurrentPattern(uia.UIA_TogglePatternId).QueryInterface(uia.IUIAutomationTogglePattern)
        if bool(toggle.CurrentToggleState) != on:
            toggle.Toggle()
            time.sleep(0.2)
        STATE["dnd"] = bool(toggle.CurrentToggleState)
        return STATE["dnd"]
    finally:
        press_keys(["esc"])
