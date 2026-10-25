"""Volume for each app (the Volume mixer), through Windows' Core Audio: which apps have sound, and how loud each is."""
import ctypes
import ctypes.wintypes
import os
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

import pc_sound


class ISimpleAudioVolume(IUnknown):
    _iid_ = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "SetMasterVolume", (["in"], ctypes.c_float), (["in"], ctypes.c_void_p)),
        COMMETHOD([], HRESULT, "GetMasterVolume", (["out"], ctypes.POINTER(ctypes.c_float))),
        COMMETHOD([], HRESULT, "SetMute", (["in"], ctypes.wintypes.BOOL), (["in"], ctypes.c_void_p)),
        COMMETHOD([], HRESULT, "GetMute", (["out"], ctypes.POINTER(ctypes.wintypes.BOOL))),
    ]


class IAudioSessionControl2(IUnknown):
    _iid_ = GUID("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetState", (["out"], ctypes.POINTER(ctypes.c_int))),
        COMMETHOD([], HRESULT, "GetDisplayName", (["out"], ctypes.POINTER(ctypes.c_wchar_p))),
        COMMETHOD([], HRESULT, "SetDisplayName", (["in"], ctypes.c_wchar_p), (["in"], ctypes.c_void_p)),
        COMMETHOD([], HRESULT, "GetIconPath", (["out"], ctypes.POINTER(ctypes.c_wchar_p))),
        COMMETHOD([], HRESULT, "SetIconPath", (["in"], ctypes.c_wchar_p), (["in"], ctypes.c_void_p)),
        COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], ctypes.POINTER(GUID))),
        COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], ctypes.POINTER(GUID)), (["in"], ctypes.c_void_p)),
        COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], ctypes.c_void_p)),
        COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], ctypes.c_void_p)),
        COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], ctypes.POINTER(ctypes.c_wchar_p))),
        COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier", (["out"], ctypes.POINTER(ctypes.c_wchar_p))),
        COMMETHOD([], HRESULT, "GetProcessId", (["out"], ctypes.POINTER(ctypes.wintypes.DWORD))),
        COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
        COMMETHOD([], HRESULT, "SetDuckingPreference", (["in"], ctypes.wintypes.BOOL)),
    ]


class IAudioSessionEnumerator(IUnknown):
    _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], ctypes.POINTER(ctypes.c_int))),
        COMMETHOD([], HRESULT, "GetSession", (["in"], ctypes.c_int), (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)))),
    ]


class IAudioSessionManager2(IUnknown):
    _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetAudioSessionControl", (["in"], ctypes.c_void_p), (["in"], ctypes.c_ulong),
                  (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)))),
        COMMETHOD([], HRESULT, "GetSimpleAudioVolume", (["in"], ctypes.c_void_p), (["in"], ctypes.c_ulong),
                  (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)))),
        COMMETHOD([], HRESULT, "GetSessionEnumerator", (["out"], ctypes.POINTER(ctypes.POINTER(IAudioSessionEnumerator)))),
    ]


ACTIVE, EXPIRED = 1, 2  # AudioSessionState


def _sessions():
    """(process id, its program's path, ISimpleAudioVolume) for each app with sound on the speakers."""
    try:
        ctypes.oledll.ole32.CoInitializeEx(None, 0)
    except OSError:
        pass
    enumerator = comtypes.CoCreateInstance(pc_sound.ENUMERATOR, pc_sound.IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
    device = enumerator.GetDefaultAudioEndpoint(0, 1)  # the speakers, for music and videos
    manager = device.Activate(ctypes.byref(IAudioSessionManager2._iid_), comtypes.CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)
    sessions = manager.GetSessionEnumerator()
    found = []
    for n in range(sessions.GetCount()):
        control = sessions.GetSession(n).QueryInterface(IAudioSessionControl2)
        if control.GetState() == EXPIRED:
            continue
        pid = control.GetProcessId()
        if not pid:
            continue  # Windows' own system sounds
        found.append((pid, program_path(pid), control.QueryInterface(ISimpleAudioVolume)))
    return found


def program_path(pid: int) -> str:
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return ""
    try:
        size, path = ctypes.wintypes.DWORD(1024), ctypes.create_unicode_buffer(1024)
        return path.value if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)) else ""
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def apps() -> list:
    """[{"path", "volume": 0-100, "muted"}], one for each program (a browser's tabs together)."""
    by_program = {}
    for pid, path, volume in _sessions():
        key = path.lower() or str(pid)
        level, muted = round(volume.GetMasterVolume() * 100), bool(volume.GetMute())
        if key in by_program:  # several sessions of one app: the loudest speaks for it
            by_program[key]["volume"] = max(by_program[key]["volume"], level)
            by_program[key]["muted"] = by_program[key]["muted"] and muted
        else:
            by_program[key] = {"path": path, "volume": level, "muted": muted}
    return list(by_program.values())


def set_app(path: str, volume: int = None, muted: bool = None) -> bool:
    """Sets every session of the program at path; True when there was one."""
    changed = False
    for _pid, found, session in _sessions():
        if found and os.path.normcase(found) == os.path.normcase(path):
            if volume is not None:
                session.SetMasterVolume(max(0, min(100, int(volume))) / 100, None)
            if muted is not None:
                session.SetMute(bool(muted), None)
            changed = True
    return changed
