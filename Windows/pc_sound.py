"""The PC's sound, for the phone to hear while it shows the PC's screen.

Windows records what the speakers play ("loopback"), through its own Core Audio, so nothing needs installing.
What it gives (32-bit floats at 44.1 or 48 kHz, two or more channels) becomes left and right at about 24 kHz:
averaged pairs, so it doesn't buzz, and small enough for the Wi-Fi. Nothing comes while nothing plays.
"""
import ctypes
import ctypes.wintypes
import threading
import warnings
from array import array

from PIL import Image

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

TARGET_RATE = 24000
LOOPBACK = 0x00020000  # AUDCLNT_STREAMFLAGS_LOOPBACK
SILENT = 0x2  # AUDCLNT_BUFFERFLAGS_SILENT
BUFFER = 2_000_000  # 200 ms, in 100 ns steps
FLOAT, EXTENSIBLE = 3, 0xFFFE


class Unavailable(Exception):
    """This PC's sound can't be listened to (no speakers, or Windows said no)."""


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", ctypes.wintypes.WORD), ("nChannels", ctypes.wintypes.WORD), ("nSamplesPerSec", ctypes.wintypes.DWORD),
                ("nAvgBytesPerSec", ctypes.wintypes.DWORD), ("nBlockAlign", ctypes.wintypes.WORD),
                ("wBitsPerSample", ctypes.wintypes.WORD), ("cbSize", ctypes.wintypes.WORD)]


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer", (["out"], ctypes.POINTER(ctypes.c_void_p)), (["out"], ctypes.POINTER(ctypes.c_uint32)),
                  (["out"], ctypes.POINTER(ctypes.c_ulong)), (["out"], ctypes.POINTER(ctypes.c_ulonglong)),
                  (["out"], ctypes.POINTER(ctypes.c_ulonglong))),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], ctypes.c_uint32)),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], ctypes.POINTER(ctypes.c_uint32))),
    ]


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Initialize", (["in"], ctypes.c_int), (["in"], ctypes.c_ulong), (["in"], ctypes.c_longlong),
                  (["in"], ctypes.c_longlong), (["in"], ctypes.c_void_p), (["in"], ctypes.c_void_p)),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], ctypes.POINTER(ctypes.c_uint32))),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], ctypes.POINTER(ctypes.c_longlong))),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], ctypes.POINTER(ctypes.c_uint32))),
        COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], ctypes.c_int), (["in"], ctypes.c_void_p),
                  (["out"], ctypes.POINTER(ctypes.c_void_p))),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], ctypes.POINTER(ctypes.c_void_p))),
        COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], ctypes.POINTER(ctypes.c_longlong)),
                  (["out"], ctypes.POINTER(ctypes.c_longlong))),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], ctypes.c_void_p)),
        COMMETHOD([], HRESULT, "GetService", (["in"], ctypes.POINTER(GUID)), (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)))),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [COMMETHOD([], HRESULT, "Activate", (["in"], ctypes.POINTER(GUID)), (["in"], ctypes.c_ulong),
                           (["in"], ctypes.c_void_p), (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown))))]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], ctypes.c_int), (["in"], ctypes.c_ulong),
                  (["out"], ctypes.POINTER(ctypes.c_void_p))),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], ctypes.c_int), (["in"], ctypes.c_int),
                  (["out"], ctypes.POINTER(ctypes.POINTER(IMMDevice)))),
    ]


ENUMERATOR = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")


def to_stereo(raw: bytes, channels: int, step: int) -> bytes:
    """Interleaved 32-bit floats with so many channels -> left and right, every step frames averaged into one."""
    samples = array("f")
    samples.frombytes(raw[:len(raw) - len(raw) % (4 * channels)])
    left, right = samples[0::channels], samples[1::channels] if channels > 1 else samples[0::channels]
    frames = len(left) // step
    if not frames:
        return b""
    if step > 1:  # the average of each few, done by Pillow's box resize, in C
        left, right = (array("f", Image.frombytes("F", (frames * step, 1), side[:frames * step].tobytes())
                             .resize((frames, 1), Image.Resampling.BOX).tobytes()) for side in (left, right))
    out = array("f", bytes(8 * frames))
    out[0::2], out[1::2] = left[:frames], right[:frames]
    return out.tobytes()


class Loopback:
    """What the speakers play, as it plays: read() returns what has come since (maybe nothing)."""

    def __init__(self):
        try:
            ctypes.oledll.ole32.CoInitializeEx(None, 0)
        except OSError:
            pass  # this thread already has COM
        try:
            enumerator = comtypes.CoCreateInstance(ENUMERATOR, IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
            device = enumerator.GetDefaultAudioEndpoint(0, 0)  # the speakers, for everything
            self.client = device.Activate(ctypes.byref(IAudioClient._iid_), comtypes.CLSCTX_ALL, None).QueryInterface(IAudioClient)
            mix = self.client.GetMixFormat()
            try:
                shape = WAVEFORMATEX.from_address(mix)
                kind = shape.wFormatTag
                if kind == EXTENSIBLE and shape.cbSize >= 22:
                    kind = ctypes.c_uint32.from_address(mix + 24).value  # the SubFormat's first part
                if kind != FLOAT or shape.wBitsPerSample != 32:
                    raise Unavailable("Windows mixes sound in a form this can't read")
                self.channels, self.block, rate = shape.nChannels, shape.nBlockAlign, shape.nSamplesPerSec
                self.client.Initialize(0, LOOPBACK, BUFFER, 0, mix, None)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(mix))
            self.capture = self.client.GetService(ctypes.byref(IAudioCaptureClient._iid_)).QueryInterface(IAudioCaptureClient)
            self.step = max(1, round(rate / TARGET_RATE))
            self.rate = rate // self.step
            self.client.Start()
        except (OSError, comtypes.COMError, ValueError) as exc:
            raise Unavailable(str(exc)) from exc
        self._lock = threading.Lock()

    def read(self) -> bytes:
        parts = []
        with self._lock:
            while self.capture.GetNextPacketSize():
                data, frames, flags, _, _ = self.capture.GetBuffer()
                size = frames * self.block
                parts.append(bytes(size) if flags & SILENT or not data else ctypes.string_at(data, size))
                self.capture.ReleaseBuffer(frames)
        return to_stereo(b"".join(parts), self.channels, self.step) if parts else b""

    def close(self):
        try:
            self.client.Stop()
        except (OSError, comtypes.COMError):
            pass
