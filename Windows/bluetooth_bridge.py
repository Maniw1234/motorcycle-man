# Motorcycle Man over Bluetooth, for a phone with the Android app and no Wi-Fi or internet in common with the PC.
# The PC listens on Bluetooth (RFCOMM, advertised under Motorcycle Man's own service id, so the app finds it on a
# paired PC) and passes what the app sends to Motorcycle Man's own web server, so the phone page works exactly as
# on the Wi-Fi. One Bluetooth connection carries many web requests at once, each as a numbered stream:
#   frame = kind (1 byte: OPEN, DATA or CLOSE), stream (2 bytes), length (4 bytes), then that many bytes.
# The phone still needs the link's secret, its requests reach the server from 127.0.0.2, which never counts as
# "this PC" (see request_from_this_pc), and only phones paired with the PC in Windows can connect at all.
import ctypes, logging, socket, struct, threading, uuid

LOG = logging.getLogger("motorcycle_man")
SERVICE = uuid.UUID("7b4d2f18-3a54-4b9e-9c1f-6d0e5a8b2c31")
SOURCE = "127.0.0.2"  # where Bluetooth's requests come from, for the web server
OPEN, DATA, CLOSE = 1, 2, 3
HEADER = struct.Struct(">BHI")
CHUNK = 32 * 1024
FRAME_LIMIT = 1 << 20


class _GUID(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_ubyte * 16)]


class _SOCKADDR_BTH(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("addressFamily", ctypes.c_ushort), ("btAddr", ctypes.c_ulonglong), ("serviceClassId", _GUID),
                ("port", ctypes.c_ulong)]


class _SOCKET_ADDRESS(ctypes.Structure):
    _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int)]


class _CSADDR_INFO(ctypes.Structure):
    _fields_ = [("LocalAddr", _SOCKET_ADDRESS), ("RemoteAddr", _SOCKET_ADDRESS), ("iSocketType", ctypes.c_int),
                ("iProtocol", ctypes.c_int)]


class _WSAQUERYSETW(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong), ("lpszServiceInstanceName", ctypes.c_wchar_p),
                ("lpServiceClassId", ctypes.POINTER(_GUID)), ("lpVersion", ctypes.c_void_p), ("lpszComment", ctypes.c_wchar_p),
                ("dwNameSpace", ctypes.c_ulong), ("lpNSProviderId", ctypes.c_void_p), ("lpszContext", ctypes.c_wchar_p),
                ("dwNumberOfProtocols", ctypes.c_ulong), ("lpafpProtocols", ctypes.c_void_p),
                ("lpszQueryString", ctypes.c_wchar_p), ("dwNumberOfCsAddrs", ctypes.c_ulong),
                ("lpcsaBuffer", ctypes.POINTER(_CSADDR_INFO)), ("dwOutputFlags", ctypes.c_ulong), ("lpBlob", ctypes.c_void_p)]


class ServiceRecord:
    # Windows' record of the service, so a phone can find the channel by the service id (WSASetService).
    AF_BTH, NS_BTH, BTHPROTO_RFCOMM, REGISTER, DELETE = 32, 16, 3, 0, 2

    def __init__(self, channel: int):
        self.service = _GUID((ctypes.c_ubyte * 16).from_buffer_copy(SERVICE.bytes_le))
        self.address = _SOCKADDR_BTH(self.AF_BTH, 0, _GUID(), channel)
        self.csaddr = _CSADDR_INFO(_SOCKET_ADDRESS(ctypes.addressof(self.address), ctypes.sizeof(self.address)),
                                   _SOCKET_ADDRESS(None, 0), socket.SOCK_STREAM, self.BTHPROTO_RFCOMM)
        self.query = _WSAQUERYSETW(ctypes.sizeof(_WSAQUERYSETW), "Motorcycle Man", ctypes.pointer(self.service), None,
                                   "Motorcycle Man over Bluetooth", self.NS_BTH, None, None, 0, None, None, 1,
                                   ctypes.pointer(self.csaddr), 0, None)
        self.ws2 = ctypes.WinDLL("ws2_32")
        self.ws2.WSASetServiceW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong)

    def register(self):
        if self.ws2.WSASetServiceW(ctypes.byref(self.query), self.REGISTER, 0):
            raise OSError(f"Windows wouldn't advertise the Bluetooth service (error {self.ws2.WSAGetLastError()})")

    def delete(self):
        self.ws2.WSASetServiceW(ctypes.byref(self.query), self.DELETE, 0)


def read_exactly(link, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = link.recv(count - len(data))
        if not chunk:
            raise ConnectionError("the phone went away")
        data += chunk
    return bytes(data)


class Mux:
    # One phone's Bluetooth connection, as many web requests to the server at once.
    def __init__(self, link, port: int, connect=None):
        self.link, self.port = link, port
        self.connect = connect or self._connect
        self.streams, self.lock = {}, threading.Lock()

    def _connect(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind((SOURCE, 0))  # never "this PC" to the web server
        server.connect(("127.0.0.1", self.port))
        return server

    def send(self, kind: int, stream: int, payload: bytes = b""):
        with self.lock:
            self.link.sendall(HEADER.pack(kind, stream, len(payload)) + payload)

    def run(self):
        try:
            while True:
                kind, stream, length = HEADER.unpack(read_exactly(self.link, HEADER.size))
                if length > FRAME_LIMIT:
                    raise ConnectionError("a frame too big")
                payload = read_exactly(self.link, length) if length else b""
                if kind == OPEN:
                    self._open(stream)
                elif kind == DATA and stream in self.streams:
                    try:
                        self.streams[stream].sendall(payload)
                    except OSError:
                        self._close(stream, tell=True)
                elif kind == CLOSE:
                    self._close(stream, tell=False)
        except (OSError, ConnectionError, struct.error):
            pass
        finally:
            for stream in list(self.streams):
                self._close(stream, tell=False)
            try:
                self.link.close()
            except OSError:
                pass

    def _open(self, stream: int):
        try:
            server = self.connect()
        except OSError:
            self.send(CLOSE, stream)
            return
        self.streams[stream] = server
        threading.Thread(target=self._pump, args=(stream, server), daemon=True).start()

    def _pump(self, stream: int, server):
        # The server's answer, back to the phone.
        try:
            while True:
                data = server.recv(CHUNK)
                if not data:
                    break
                self.send(DATA, stream, data)
        except OSError:
            pass
        self._close(stream, tell=True)

    def _close(self, stream: int, tell: bool):
        server = self.streams.pop(stream, None)
        if server is None:
            return
        try:
            server.close()
        except OSError:
            pass
        if tell:
            try:
                self.send(CLOSE, stream)
            except OSError:
                pass


class BluetoothBridge:
    # Listens while it's on; changed(state, detail): "on" (detail: the PC's Bluetooth name), "off", "failed".
    def __init__(self, port: int, changed=lambda state, detail: None):
        self.port, self.changed = port, changed
        self.server = self.record = self.thread = None
        self.stopped = threading.Event()
        self.state, self.detail = "off", ""

    def _tell(self, state: str, detail: str = ""):
        self.state, self.detail = state, detail
        self.changed(state, detail)

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.stopped.clear()
        self.thread = threading.Thread(target=self.run, name="bluetooth", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.record is not None:
            self.record.delete()
            self.record = None
        if self.server is not None:
            try:
                self.server.close()
            except OSError:
                pass
        self._tell("off")

    def run(self):
        try:
            self.server = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            self.server.bind(("00:00:00:00:00:00", -1))  # any adapter, any free channel
            self.server.listen(2)
            adapter, channel = self.server.getsockname()
            self.record = ServiceRecord(channel)
            self.record.register()
        except (OSError, AttributeError) as exc:
            LOG.warning("Bluetooth isn't available: %s", exc)
            self._tell("failed", "This PC's Bluetooth is off or missing.")
            return
        LOG.info("Listening on Bluetooth (channel %s)", channel)
        self._tell("on", adapter)
        while not self.stopped.is_set():
            try:
                link, phone = self.server.accept()
            except OSError:
                break
            LOG.info("A phone connected over Bluetooth")
            threading.Thread(target=Mux(link, self.port).run, daemon=True).start()
