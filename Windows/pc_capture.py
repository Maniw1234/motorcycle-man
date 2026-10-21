# Fast pictures of the screen through Windows' Desktop Duplication (DXGI), the way screen-sharing apps get them:
# a copy of the screen in a few milliseconds, and Windows says when something changed, so an unchanged screen
# costs nothing and a change shows at once. PIL's ImageGrab took about 50 ms a picture on a 2560x1600 screen
# (October 2026), most of the screen view's delay, and the view looked for changes only every 0.15 s. Where
# duplication isn't available (a remote session, an old driver, the lock screen), ScreenSource falls back to
# ImageGrab and compares small thumbnails to see changes.
import ctypes
import ctypes.wintypes as wt
import threading
import time
import uuid

from PIL import Image, ImageChops, ImageGrab

HRESULT = ctypes.HRESULT
WAIT_TIMEOUT, ACCESS_LOST = 0x887A0027, 0x887A0026
D3D11_SDK_VERSION, D3D_DRIVER_TYPE_UNKNOWN = 7, 0
D3D11_USAGE_STAGING, D3D11_CPU_ACCESS_READ, D3D11_MAP_READ = 3, 0x20000, 1


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def guid(text: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


IID_FACTORY1 = guid("770aae78-f26f-4dba-a829-253c83d1b387")
IID_OUTPUT1 = guid("00cddea8-939b-4b83-a340-a685226666cc")
IID_TEXTURE2D = guid("6f15aaf2-d208-4e89-9ab4-489535d34f9c")


class OUTPUT_DESC(ctypes.Structure):
    _fields_ = [("DeviceName", ctypes.c_wchar * 32), ("DesktopCoordinates", wt.RECT), ("AttachedToDesktop", wt.BOOL),
                ("Rotation", ctypes.c_uint), ("Monitor", wt.HMONITOR)]


class FRAME_INFO(ctypes.Structure):
    _fields_ = [("LastPresentTime", ctypes.c_longlong), ("LastMouseUpdateTime", ctypes.c_longlong),
                ("AccumulatedFrames", ctypes.c_uint), ("RectsCoalesced", wt.BOOL), ("ProtectedContentMaskedOut", wt.BOOL),
                ("PointerPosition", wt.POINT), ("PointerVisible", wt.BOOL), ("TotalMetadataBufferSize", ctypes.c_uint),
                ("PointerShapeBufferSize", ctypes.c_uint)]


class TEXTURE2D_DESC(ctypes.Structure):
    _fields_ = [("Width", ctypes.c_uint), ("Height", ctypes.c_uint), ("MipLevels", ctypes.c_uint), ("ArraySize", ctypes.c_uint),
                ("Format", ctypes.c_uint), ("SampleCount", ctypes.c_uint), ("SampleQuality", ctypes.c_uint),
                ("Usage", ctypes.c_uint), ("BindFlags", ctypes.c_uint), ("CPUAccessFlags", ctypes.c_uint), ("MiscFlags", ctypes.c_uint)]


class MAPPED(ctypes.Structure):
    _fields_ = [("pData", ctypes.c_void_p), ("RowPitch", ctypes.c_uint), ("DepthPitch", ctypes.c_uint)]


def call(obj, index: int, restype, argtypes, *args):
    # Calls method number index of a COM object (its place in the interface, counting IUnknown's three).
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])(obj, *args)


def release(obj):
    if obj:
        call(obj, 2, ctypes.c_ulong, ())


def query(obj, iid: GUID) -> ctypes.c_void_p:
    found = ctypes.c_void_p()
    call(obj, 0, HRESULT, (ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)), ctypes.byref(iid), ctypes.byref(found))
    return found


def code_of(error: OSError) -> int:
    return (getattr(error, "winerror", 0) or 0) & 0xFFFFFFFF


class Duplication:
    # The primary screen through Desktop Duplication. wait(ms) is True when a new picture of the screen is in
    # .latest (a PIL RGB image), False when nothing changed meanwhile (the pointer moving doesn't count).
    def __init__(self):
        self.device = self.context = self.dup = self.staging = None
        self.size, self.latest = (0, 0), None
        self._open()

    def _open(self):
        dxgi, d3d11 = ctypes.WinDLL("dxgi"), ctypes.WinDLL("d3d11")
        user32 = ctypes.WinDLL("user32")
        user32.MonitorFromPoint.argtypes, user32.MonitorFromPoint.restype = (wt.POINT, wt.DWORD), wt.HMONITOR
        primary = user32.MonitorFromPoint(wt.POINT(0, 0), 1)  # MONITOR_DEFAULTTOPRIMARY
        factory = ctypes.c_void_p()
        dxgi.CreateDXGIFactory1.argtypes = (ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
        dxgi.CreateDXGIFactory1.restype = HRESULT
        dxgi.CreateDXGIFactory1(ctypes.byref(IID_FACTORY1), ctypes.byref(factory))
        try:
            for a in range(16):
                adapter = ctypes.c_void_p()
                try:
                    call(factory, 7, HRESULT, (ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)), a, ctypes.byref(adapter))
                except OSError:
                    break  # no more graphics adapters
                try:
                    for o in range(16):
                        output = ctypes.c_void_p()
                        try:
                            call(adapter, 7, HRESULT, (ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)), o, ctypes.byref(output))
                        except OSError:
                            break  # no more screens on this adapter
                        try:
                            desc = OUTPUT_DESC()
                            call(output, 7, HRESULT, (ctypes.POINTER(OUTPUT_DESC),), ctypes.byref(desc))
                            if desc.AttachedToDesktop and desc.Monitor == primary:
                                self._start(d3d11, adapter, output, desc)
                                return
                        finally:
                            release(output)
                finally:
                    release(adapter)
        finally:
            release(factory)
        raise OSError("the primary screen wasn't found")

    def _start(self, d3d11, adapter, output, desc):
        if desc.Rotation not in (0, 1):  # turned sideways: the plain way handles that
            raise OSError("the screen is rotated")
        d3d11.D3D11CreateDevice.restype = HRESULT
        d3d11.D3D11CreateDevice.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                            ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_void_p))
        self.device, self.context = ctypes.c_void_p(), ctypes.c_void_p()
        d3d11.D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, None, 0, None, 0, D3D11_SDK_VERSION,
                                ctypes.byref(self.device), None, ctypes.byref(self.context))
        output1 = query(output, IID_OUTPUT1)
        try:
            self.dup = ctypes.c_void_p()
            call(output1, 22, HRESULT, (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)), self.device, ctypes.byref(self.dup))
        finally:
            release(output1)
        rect = desc.DesktopCoordinates
        self.size = (rect.right - rect.left, rect.bottom - rect.top)

    def wait(self, timeout_ms: int) -> bool:
        info, resource = FRAME_INFO(), ctypes.c_void_p()
        try:
            call(self.dup, 8, HRESULT, (ctypes.c_uint, ctypes.POINTER(FRAME_INFO), ctypes.POINTER(ctypes.c_void_p)),
                 int(timeout_ms), ctypes.byref(info), ctypes.byref(resource))
        except OSError as error:
            if code_of(error) == WAIT_TIMEOUT:
                return False
            raise  # access lost (the lock screen, a resolution change...): the caller opens a new one
        try:
            if info.LastPresentTime == 0 and self.latest is not None:
                return False  # only the pointer moved
            texture = query(resource, IID_TEXTURE2D)
            try:
                if self.staging is None:
                    self._make_staging(texture)
                call(self.context, 47, None, (ctypes.c_void_p, ctypes.c_void_p), self.staging, texture)  # CopyResource
            finally:
                release(texture)
        finally:
            release(resource)
            call(self.dup, 14, HRESULT, ())  # ReleaseFrame
        mapped = MAPPED()
        call(self.context, 14, HRESULT, (ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(MAPPED)),
             self.staging, 0, D3D11_MAP_READ, 0, ctypes.byref(mapped))  # Map
        try:
            width, height = self.size
            pixels = (ctypes.c_char * (mapped.RowPitch * height)).from_address(mapped.pData)
            picture = Image.frombuffer("RGB", (width, height), pixels, "raw", "BGRX", mapped.RowPitch, 1).copy()
        finally:
            call(self.context, 15, None, (ctypes.c_void_p, ctypes.c_uint), self.staging, 0)  # Unmap
        if self.latest is None and picture.getbbox() is None:
            picture = ImageGrab.grab()  # the very first copy came out black on this PC; the plain way fills in for it
        self.latest = picture
        return True

    def _make_staging(self, texture):
        desc = TEXTURE2D_DESC()
        call(texture, 10, None, (ctypes.POINTER(TEXTURE2D_DESC),), ctypes.byref(desc))  # GetDesc
        self.size = (desc.Width, desc.Height)
        staging = TEXTURE2D_DESC(desc.Width, desc.Height, 1, 1, desc.Format, 1, 0, D3D11_USAGE_STAGING, 0,
                                 D3D11_CPU_ACCESS_READ, 0)
        self.staging = ctypes.c_void_p()
        call(self.device, 5, HRESULT, (ctypes.POINTER(TEXTURE2D_DESC), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
             ctypes.byref(staging), None, ctypes.byref(self.staging))  # CreateTexture2D

    def close(self):
        for name in ("staging", "dup", "context", "device"):
            release(getattr(self, name))
            setattr(self, name, None)


class ScreenSource:
    # Pictures of the primary screen for one helper thread: Desktop Duplication when Windows offers it, else
    # ImageGrab. wait_for_change(seconds) is True when .latest holds a new picture.
    RETRY_SECONDS = 5.0

    def __init__(self, duplication=None, grab=None):
        self._make = duplication or (lambda: Duplication())  # looked up when used, so tests can stand in for it
        self._grab = grab or (lambda: ImageGrab.grab())
        self.duplication, self.latest, self.method, self._thumb, self._retry_at = None, None, "", None, 0.0
        self._lock = threading.Lock()

    def wait_for_change(self, seconds: float) -> bool:
        with self._lock:
            if self.duplication is None and time.monotonic() >= self._retry_at:
                try:
                    self.duplication, self.method = self._make(), "duplication"
                except OSError:
                    self._retry_at = time.monotonic() + self.RETRY_SECONDS
            if self.duplication is not None:
                try:
                    changed = self.duplication.wait(max(1, int(seconds * 1000)))
                except OSError:
                    self.duplication.close()
                    self.duplication, self._retry_at = None, time.monotonic() + 0.5
                    return False
                if changed:
                    self.latest = self.duplication.latest
                return changed
            return self._grab_and_compare(seconds)

    def _grab_and_compare(self, seconds: float) -> bool:
        started, self.method = time.monotonic(), "grab"
        try:
            shot = self._grab()
        except OSError:
            time.sleep(seconds)
            return False
        thumb = shot.reduce(8)
        changed = self._thumb is None or thumb.size != self._thumb.size or ImageChops.difference(thumb, self._thumb).getbbox() is not None
        if changed:
            self.latest, self._thumb = shot, thumb
            return True
        time.sleep(max(0.0, seconds - (time.monotonic() - started)))
        return False

    def close(self):
        with self._lock:
            if self.duplication is not None:
                self.duplication.close()
                self.duplication = None
