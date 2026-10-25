"""The PC's power on a timer, and waking it from the phone.

Shut down, restart, sleep or lock in a while ("in 30 minutes") or at a time ("at 11 pm"), with a warning a
minute before shutting down or restarting and a way to cancel. A shutdown asked for without a time waits a
minute too, so unsaved work can be saved. The timer lives while Motorcycle Man runs.

Waking: the phone sends a Wake-on-LAN "magic packet" to the PC's network card, so it needs the card's
address and the Wi-Fi's broadcast address, which only the PC knows. They are told only to a phone on the
same Wi-Fi, and Windows has to let the card wake the PC (Device Manager: the card's Power Management).
"""
import ctypes
import ctypes.wintypes
import ipaddress
import re
import subprocess
import threading
import time

import reminders

ACTIONS = ("shutdown", "restart", "sleep", "lock")
WILL = {"shutdown": "shut down", "restart": "restart", "sleep": "go to sleep", "lock": "lock"}
DOING = {"shutdown": "Shutting down", "restart": "Restarting", "sleep": "Going to sleep", "lock": "Locking"}
WARN_BEFORE = 60  # seconds: the warning before a shutdown or restart, and the wait when no time was said
LONGEST = 24 * 3600

_LEAD = r"(?:(?:hey|ok|okay|please|can you|could you|would you|motorcycle man|just)[,]?\s+)*"
_PC = r"(?P<pc>\s+(?:the\s+|my\s+|this\s+)?(?:pc|computer|laptop|machine))?"
REQUEST = re.compile(
    _LEAD + r"(?:(?P<shutdown>shut\s*down|turn\s+off|power\s+off|switch\s+off)|(?P<restart>restart|reboot)|"
    r"(?P<sleep>(?:go\s+to\s+)?sleep)|(?P<lock>lock))" + _PC +
    r"(?:\s+(?P<when>(?:in|after|at|tonight|tomorrow)\b.*))?", re.IGNORECASE)
PUT_TO_SLEEP = re.compile(_LEAD + r"put" + _PC + r"\s+to\s+sleep(?:\s+(?P<when>(?:in|after|at|tonight|tomorrow)\b.*))?",
                          re.IGNORECASE)
CANCEL = re.compile(
    _LEAD + r"(?:cancel|stop|abort|don'?t|do\s+not|never\s*mind)\s+(?:the\s+|my\s+)?(?:pc'?s?\s+|computer'?s?\s+)?"
    r"(?:shut\s*down|shutting\s+down|turn(?:ing)?\s+off|restart(?:ing)?|reboot(?:ing)?|(?:going\s+to\s+)?sleep|"
    r"lock(?:ing)?|power)(?:\s+timer)?(?:\s+(?:the\s+)?(?:pc|computer|laptop))?", re.IGNORECASE)


def understand(text: str, now: float = None):
    """("cancel", None), (action, due time), (action, None) when the time wasn't understood, or None."""
    words = " ".join(str(text).replace("’", "'").split()).strip(" .!?")
    if CANCEL.fullmatch(words):
        return "cancel", None
    now = time.time() if now is None else now
    found = PUT_TO_SLEEP.fullmatch(words)
    if found:
        action = "sleep"
    else:
        found = REQUEST.fullmatch(words)
        if not found:
            return None
        action = next(name for name in ACTIONS if found[name])
        if not found["pc"] and not found["when"] and (action in ("sleep", "lock") or not found[action].lower().startswith(
                ("shut", "restart", "reboot"))):
            return None  # "sleep", "lock" or "turn off" alone could mean anything
    if not found["when"]:
        if action in ("sleep", "lock"):
            return None  # right away: the remote's own buttons and commands do that
        return action, now + WARN_BEFORE
    due, left = reminders.when_from(found["when"], now)
    if due is None or left.strip(" ,.") or due <= now:
        return action, None
    return action, due


class PowerTimer:
    """One power action at a time; a new one replaces the old. do(action) carries it out, and
    warn(text) tells whoever is at the PC (and the phone) that it is coming."""

    def __init__(self, do, warn):
        self._do, self._warn = do, warn
        self._lock = threading.Lock()
        self._timers = []
        self.action, self.due = None, 0.0

    def set(self, action: str, due: float, now: float = None) -> str:
        now = time.time() if now is None else now
        if action not in ACTIONS:
            raise ValueError(action)
        if due - now > LONGEST:
            return "I can set the PC's power up to a day ahead."
        with self._lock:
            self._stop_locked()
            self.action, self.due = action, due
            wait = max(0.0, due - now)
            self._start_locked(wait, self._fire, (action, due))
            if action in ("shutdown", "restart") and wait > WARN_BEFORE + 5:
                self._start_locked(wait - WARN_BEFORE, self._warn_soon, (action, due))
        if action in ("shutdown", "restart") and wait <= WARN_BEFORE + 5:
            self._warn_soon(action, due)
        return self.words(now) + f' Say "cancel {action}" to stop it.'

    def cancel(self) -> str:
        with self._lock:
            was = self.action
            self._stop_locked()
        return f"Okay, the PC won't {WILL[was]}." if was else "There's no power timer set."

    def status(self, now: float = None) -> dict:
        with self._lock:
            return {"action": self.action, "due": self.due, "say": self.words(now) if self.action else ""}

    def words(self, now: float = None) -> str:
        now = time.time() if now is None else now
        left = max(0.0, self.due - now)
        when = f"in {reminders.duration_words(max(60, round(left / 60) * 60))}" if left < 3600 else \
            f"at {reminders.clock_words(self.due, now)}"
        return f"{DOING[self.action]} {when}."

    def _start_locked(self, wait, target, args):
        timer = threading.Timer(wait, target, args=args)
        timer.daemon = True
        timer.start()
        self._timers.append(timer)

    def _stop_locked(self):
        for timer in self._timers:
            timer.cancel()
        self._timers, self.action, self.due = [], None, 0.0

    def _still(self, action, due) -> bool:
        return (self.action, self.due) == (action, due)

    def _warn_soon(self, action, due):
        with self._lock:
            if not self._still(action, due):
                return
        self._warn(f"{DOING[action]} in a minute: save your work. Say \"cancel {action}\" or tap Cancel on the phone to stop it.")

    def _fire(self, action, due):
        with self._lock:
            if not self._still(action, due):
                return
            self._timers, self.action, self.due = [], None, 0.0
        self._do(action)


# --- waking from the phone ---

class IP_ADDR_STRING(ctypes.Structure):
    pass


IP_ADDR_STRING._fields_ = [("Next", ctypes.POINTER(IP_ADDR_STRING)), ("IpAddress", ctypes.c_char * 16),
                           ("IpMask", ctypes.c_char * 16), ("Context", ctypes.wintypes.DWORD)]


class IP_ADAPTER_INFO(ctypes.Structure):
    pass


IP_ADAPTER_INFO._fields_ = [
    ("Next", ctypes.POINTER(IP_ADAPTER_INFO)), ("ComboIndex", ctypes.wintypes.DWORD), ("AdapterName", ctypes.c_char * 260),
    ("Description", ctypes.c_char * 132), ("AddressLength", ctypes.c_uint), ("Address", ctypes.c_ubyte * 8),
    ("Index", ctypes.wintypes.DWORD), ("Type", ctypes.c_uint), ("DhcpEnabled", ctypes.c_uint), ("CurrentIpAddress", ctypes.c_void_p),
    ("IpAddressList", IP_ADDR_STRING), ("GatewayList", IP_ADDR_STRING), ("DhcpServer", IP_ADDR_STRING),
    ("HaveWins", ctypes.wintypes.BOOL), ("PrimaryWinsServer", IP_ADDR_STRING), ("SecondaryWinsServer", IP_ADDR_STRING),
    ("LeaseObtained", ctypes.c_longlong), ("LeaseExpires", ctypes.c_longlong)]


def network_cards() -> list:
    """(card address "AA:BB:CC:DD:EE:FF", IPv4 address, mask, the card's name) for each address of each card."""
    size = ctypes.c_ulong(0)
    iphlpapi = ctypes.windll.iphlpapi
    iphlpapi.GetAdaptersInfo(None, ctypes.byref(size))  # says how much room the list needs
    if not size.value:
        return []
    room = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetAdaptersInfo(room, ctypes.byref(size)):
        return []
    found, card = [], ctypes.cast(room, ctypes.POINTER(IP_ADAPTER_INFO))
    while card:
        info = card.contents
        mac = ":".join(f"{b:02X}" for b in info.Address[:info.AddressLength])
        address = info.IpAddressList
        while True:
            found.append((mac, address.IpAddress.decode(errors="replace"), address.IpMask.decode(errors="replace"),
                          info.Description.decode(errors="replace")))
            if not address.Next:
                break
            address = address.Next.contents
        card = info.Next
    return found


def cards_that_wake() -> list:
    """The names of the devices Windows lets wake the PC, or None when it won't say."""
    try:
        listed = subprocess.run(["powercfg", "/devicequery", "wake_armed"], capture_output=True, text=True, timeout=10,
                                creationflags=0x08000000)  # CREATE_NO_WINDOW
    except (OSError, subprocess.SubprocessError):
        return None
    return [line.strip() for line in listed.stdout.splitlines() if line.strip()] if listed.returncode == 0 else None


def wake_info(local_ip: str) -> dict:
    """What a phone that reached the PC at local_ip needs to wake it: {"mac", "broadcast", "card", "can_wake"},
    or {} when local_ip isn't on a home or office network (the away-from-home link arrives as 127.0.0.1)."""
    try:
        reached = ipaddress.IPv4Address(local_ip)
    except ValueError:
        return {}
    if not reached.is_private or reached.is_loopback or reached.is_link_local:
        return {}
    for mac, address, mask, card in network_cards():
        if address == local_ip and re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
            network = ipaddress.IPv4Network(f"{address}/{mask}", strict=False)
            waking = cards_that_wake()
            return {"mac": mac, "broadcast": str(network.broadcast_address), "card": card,
                    "can_wake": None if waking is None else card in waking}
    return {}
