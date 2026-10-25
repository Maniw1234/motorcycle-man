# Motorcycle Man on Discord: the user's own bot, made for free in Discord's developer portal. Motorcycle Man
# connects out to Discord's gateway (nothing on the PC is opened to the internet) and answers direct messages
# through chat_link. The bot's key is a secret: it is never logged or shown.
import datetime as dt, json, logging, threading, time, urllib.error, urllib.request, uuid

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

LOG = logging.getLogger("motorcycle_man")
API = "https://discord.com/api/v10"
GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
INTENTS = 1 << 12  # direct messages, whose words come without Discord's privileged message-content switch
USER_AGENT = "DiscordBot (https://github.com/Maniw1234/motorcycle-man, 1.0)"
VOICE_MESSAGE = 1 << 13  # a message's flag for a recorded voice message
FILE_LIMIT = 25 << 20
MESSAGE_LIMIT = 2000
BAD_KEY_CODES = {4004}  # the gateway's "authentication failed"


class DiscordError(RuntimeError):
    def __init__(self, description, code=0):
        super().__init__(description)
        self.code = code


def call(token: str, method: str, path: str, body=None, files=None, timeout=20):
    # One REST call; files maps a field to (file name, bytes, media type), sent with body as payload_json.
    headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}
    data = None
    if files:
        boundary = uuid.uuid4().hex
        out = bytearray(f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
                        f"Content-Type: application/json\r\n\r\n{json.dumps(body or {})}\r\n".encode())
        for field, (file_name, content, kind) in files.items():
            out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{file_name}"\r\n'
                    f"Content-Type: {kind}\r\n\r\n").encode() + content + b"\r\n"
        out += f"--{boundary}--\r\n".encode()
        data, headers["Content-Type"] = bytes(out), f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    for attempt in (1, 2):
        request = urllib.request.Request(API + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                raw = reply.read()
            return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:  # its address is harmless, but its request held the key
            try:
                answer = json.loads(exc.read() or b"{}")
            except ValueError:
                answer = {}
            finally:
                exc.close()
            if exc.code == 429 and attempt == 1:  # too fast: Discord says how long to wait
                time.sleep(min(5.0, float(answer.get("retry_after") or 1)))
                continue
            raise DiscordError(str(answer.get("message") or f"Discord answered {exc.code}"), exc.code) from None
    return None


def download(url: str, limit: int = FILE_LIMIT) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=60) as reply:
        data = reply.read(limit + 1)
    if len(data) > limit:
        raise DiscordError("too big")
    return data


def check_key(token: str):
    # (the bot's name, its application's id), or DiscordError when Discord refuses the key.
    me = call(token, "GET", "/users/@me")
    return str(me.get("username") or ""), str(me.get("id") or "")


def invite_link(app_id: str) -> str:
    # Adds the bot to a server of the user's (it needs one shared server before it can be messaged).
    return f"https://discord.com/oauth2/authorize?client_id={app_id}&scope=bot&permissions=0"


class DiscordBot:
    def __init__(self, token: str, link):
        self.token, self.link = token, link
        link.transport = self
        self.name = self.app_id = ""
        self.stopped = threading.Event()
        self.thread = self.ws = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name="discord", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        ws = self.ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def run(self):
        delay, failed = 2, False
        while not self.stopped.is_set():
            try:
                self.session()
                delay, failed = 2, False
            except ConnectionClosed as exc:
                code = exc.rcvd.code if exc.rcvd is not None else 0
                if code in BAD_KEY_CODES:
                    self.link.helper.changed("bad key", "Discord doesn't know this bot key. Set it up again.")
                    return
                failed = self._wait(failed, delay)
                delay = min(delay * 2, 60)
            except (OSError, ValueError, TimeoutError, KeyError, DiscordError):
                failed = self._wait(failed, delay)
                delay = min(delay * 2, 60)

    def _wait(self, failed: bool, delay: float) -> bool:
        if self.stopped.is_set():
            return failed
        if not failed:
            LOG.warning("Discord bot: can't reach Discord, trying again")
            self.link.helper.changed("failed", "Can't reach Discord. Trying again...")
        self.stopped.wait(delay)
        return True

    def session(self):
        # One connection to the gateway: hello, heartbeats, identify, then events until it closes.
        with connect(GATEWAY, open_timeout=20, close_timeout=5, user_agent_header=USER_AGENT, max_size=1 << 22) as ws:
            self.ws = ws
            if self.stopped.is_set():
                return
            hello = json.loads(ws.recv(timeout=30))
            interval = float(hello["d"]["heartbeat_interval"]) / 1000
            sequence, beating = {"n": None}, threading.Event()

            def beat():
                while not beating.wait(interval):
                    try:
                        ws.send(json.dumps({"op": 1, "d": sequence["n"]}))
                    except Exception:
                        return

            threading.Thread(target=beat, daemon=True).start()
            try:
                ws.send(json.dumps({"op": 2, "d": {"token": self.token, "intents": INTENTS, "properties": {
                    "os": "windows", "browser": "motorcycle-man", "device": "motorcycle-man"}}}))
                for raw in ws:
                    if self.stopped.is_set():
                        return
                    event = json.loads(raw)
                    if event.get("s") is not None:
                        sequence["n"] = event["s"]
                    if event.get("op") == 0:
                        try:
                            self.dispatch(event.get("t"), event.get("d") or {})
                        except Exception:
                            LOG.exception("A Discord message failed")
                    elif event.get("op") == 1:  # Discord asks for a heartbeat now
                        ws.send(json.dumps({"op": 1, "d": sequence["n"]}))
                    elif event.get("op") in (7, 9):  # reconnect, or the session ended: start again
                        return
            finally:
                beating.set()
                self.ws = None

    def dispatch(self, kind: str, data: dict):
        if kind == "READY":
            user = data.get("user") or {}
            self.name, self.app_id = str(user.get("username") or ""), str(user.get("id") or "")
            self.link.helper.changed("on", self.name)
            return
        if kind != "MESSAGE_CREATE":
            return
        author = data.get("author") or {}
        if author.get("bot") or data.get("guild_id"):
            return  # only direct messages, and never from bots
        sound, files = None, []
        for item in data.get("attachments") or []:
            url, file_name = str(item.get("url") or ""), str(item.get("filename") or "file")
            if int(item.get("size") or 0) > FILE_LIMIT:
                self.link.say(data["channel_id"], f"{file_name} is too big for Discord's bots (25 MB at most).")
            elif int(data.get("flags") or 0) & VOICE_MESSAGE:
                sound = (lambda u=url: download(u), str(item.get("content_type") or "audio/ogg").split(";")[0])
            else:
                files.append((file_name, lambda u=url: download(u)))
        try:
            sent = dt.datetime.fromisoformat(str(data.get("timestamp"))).timestamp()
        except ValueError:
            sent = None
        self.link.receive(data["channel_id"], author.get("id"), data.get("content") or "",
                          str(author.get("global_name") or author.get("username") or ""), sent, sound, files)

    # --- what goes out ---

    def send(self, chat, text: str):
        for start in range(0, len(text), MESSAGE_LIMIT):
            call(self.token, "POST", f"/channels/{chat}/messages",
                 {"content": text[start:start + MESSAGE_LIMIT], "allowed_mentions": {"parse": []}})

    def send_photo(self, chat, jpeg: bytes):
        call(self.token, "POST", f"/channels/{chat}/messages", {"content": ""},
             files={"files[0]": ("screen.jpg", jpeg, "image/jpeg")}, timeout=60)

    def typing(self, chat):
        call(self.token, "POST", f"/channels/{chat}/typing", timeout=10)

    def chat_for(self, owner: str) -> str:
        return str(call(self.token, "POST", "/users/@me/channels", {"recipient_id": owner})["id"])
