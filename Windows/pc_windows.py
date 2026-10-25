"""The PC's open windows, for the phone: what the taskbar shows (minimized windows too), front to back, and each
program's icon as a small picture."""
import ctypes
import ctypes.wintypes
import io

from PIL import Image

HWND, DWORD = ctypes.wintypes.HWND, ctypes.wintypes.DWORD
user32, kernel32 = ctypes.WinDLL("user32"), ctypes.WinDLL("kernel32")
gdi32, dwmapi = ctypes.WinDLL("gdi32"), ctypes.WinDLL("dwmapi")
ENUM = ctypes.WINFUNCTYPE(ctypes.c_bool, HWND, ctypes.wintypes.LPARAM)
user32.EnumWindows.argtypes = (ENUM, ctypes.wintypes.LPARAM)
for _name in ("IsWindowVisible", "IsIconic", "GetWindowTextLengthW", "IsWindow"):
    getattr(user32, _name).argtypes = (HWND,)
user32.GetWindowTextW.argtypes = (HWND, ctypes.wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowLongW.argtypes = (HWND, ctypes.c_int)
user32.GetWindow.argtypes = (HWND, ctypes.c_uint)
user32.GetWindow.restype = HWND
user32.GetWindowThreadProcessId.argtypes = (HWND, ctypes.POINTER(DWORD))
user32.GetForegroundWindow.restype = HWND
user32.ShowWindow.argtypes = (HWND, ctypes.c_int)
user32.PostMessageW.argtypes = (HWND, ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
user32.PrivateExtractIconsW.argtypes = (ctypes.wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.POINTER(ctypes.wintypes.HICON), ctypes.POINTER(ctypes.c_uint), ctypes.c_uint,
                                        ctypes.c_uint)
user32.DrawIconEx.argtypes = (ctypes.wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.wintypes.HICON, ctypes.c_int, ctypes.c_int,
                              ctypes.c_uint, ctypes.wintypes.HBRUSH, ctypes.c_uint)
user32.DestroyIcon.argtypes = (ctypes.wintypes.HICON,)
user32.GetDC.restype = ctypes.wintypes.HDC
user32.ReleaseDC.argtypes = (HWND, ctypes.wintypes.HDC)
gdi32.CreateCompatibleDC.argtypes = (ctypes.wintypes.HDC,)
gdi32.CreateCompatibleDC.restype = ctypes.wintypes.HDC
gdi32.CreateDIBSection.argtypes = (ctypes.wintypes.HDC, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.wintypes.HANDLE, DWORD)
gdi32.CreateDIBSection.restype = ctypes.wintypes.HBITMAP
gdi32.SelectObject.argtypes = (ctypes.wintypes.HDC, ctypes.wintypes.HGDIOBJ)
gdi32.SelectObject.restype = ctypes.wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = (ctypes.wintypes.HGDIOBJ,)
gdi32.DeleteDC.argtypes = (ctypes.wintypes.HDC,)
kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (ctypes.wintypes.HANDLE, DWORD, ctypes.wintypes.LPWSTR, ctypes.POINTER(DWORD))
kernel32.CloseHandle.argtypes = (ctypes.wintypes.HANDLE,)
dwmapi.DwmGetWindowAttribute.argtypes = (HWND, DWORD, ctypes.c_void_p, DWORD)
TOOL_WINDOW, GW_OWNER, DWMWA_CLOAKED = 0x80, 4, 14
SW_MINIMIZE, SW_MAXIMIZE, WM_CLOSE = 6, 3, 0x0010


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long), ("biPlanes", ctypes.wintypes.WORD),
                ("biBitCount", ctypes.wintypes.WORD), ("biCompression", DWORD), ("biSizeImage", DWORD),
                ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", DWORD), ("biClrImportant", DWORD)]


def window_title(hwnd) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    text = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, text, length + 1)
    return text.value.strip()


def window_program(hwnd):
    """(the process id, the path of its program) for a window; the path is "" when Windows won't say."""
    pid = DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return pid.value, ""
    try:
        size, path = DWORD(1024), ctypes.create_unicode_buffer(1024)
        return pid.value, path.value if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(handle)


def taskbar_windows(own_pid: int = 0) -> list:
    """[{"hwnd", "title", "path", "minimized", "front"}], front to back: shown, titled, not owned by another
    window, not tool windows or the hidden (cloaked) windows of store apps and other desktops; not own_pid's."""
    found = []

    @ENUM
    def each(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0 and not user32.GetWindow(hwnd, GW_OWNER)
                and not user32.GetWindowLongW(hwnd, -20) & TOOL_WINDOW):  # GWL_EXSTYLE
            cloaked = DWORD()
            dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), 4)
            title = window_title(hwnd)
            if not cloaked.value and title and title != "Program Manager":
                found.append((hwnd, title))
        return True

    user32.EnumWindows(each, 0)
    front, windows = user32.GetForegroundWindow(), []
    for hwnd, title in found:
        pid, path = window_program(hwnd)
        if pid != own_pid:
            windows.append({"hwnd": int(hwnd), "title": title, "path": path, "minimized": bool(user32.IsIconic(hwnd)),
                            "front": bool(front) and int(hwnd) == int(front)})
    return windows


def minimize(hwnd):
    user32.ShowWindow(hwnd, SW_MINIMIZE)


def maximize(hwnd):
    user32.ShowWindow(hwnd, SW_MAXIMIZE)


def ask_to_close(hwnd):
    # The same as the window's ×: the app can still ask about unsaved work, on the PC.
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def icon_png(path: str, size: int = 64):
    """A program's icon as a PNG size pixels square, or None when it has none."""
    icon, icon_id = ctypes.wintypes.HICON(), ctypes.c_uint()
    if not path or user32.PrivateExtractIconsW(path, 0, size, size, ctypes.byref(icon), ctypes.byref(icon_id), 1, 0) < 1 or not icon:
        return None
    screen = user32.GetDC(None)
    canvas = gdi32.CreateCompatibleDC(screen)
    header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=size, biHeight=-size, biPlanes=1, biBitCount=32)
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(canvas, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    try:
        if not bitmap or not bits:
            return None
        old = gdi32.SelectObject(canvas, bitmap)
        user32.DrawIconEx(canvas, 0, 0, icon, size, size, 0, None, 3)  # DI_NORMAL
        gdi32.SelectObject(canvas, old)
        picture = Image.frombuffer("RGBA", (size, size), ctypes.string_at(bits, size * size * 4), "raw", "BGRA", 0, 1).copy()
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(canvas)
        user32.ReleaseDC(None, screen)
        user32.DestroyIcon(icon)
    if picture.getextrema()[3][1] == 0:  # an old icon without see-through parts drawn in
        picture.putalpha(255)
    out = io.BytesIO()
    picture.save(out, format="PNG")
    return out.getvalue()
