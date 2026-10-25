# Motorcycle Man on WhatsApp and Instagram, through Meta's free APIs: the user makes a Meta app, and Meta delivers
# each message to the PC's fixed link (/whatsapp or /instagram, see fixed_address), which answers through the same
# API. A delivery counts only when it is signed with the app's secret (X-Hub-Signature-256), so nobody else can
# send one; Meta's check of the link itself uses a verify token Motorcycle Man made. Messages then go through
# chat_link like Discord's. Keys and secrets are never logged or shown.
import hashlib, hmac, json, logging, threading, urllib.error, urllib.parse, urllib.request, uuid

LOG = logging.getLogger("motorcycle_man")
GRAPH = "https://graph.facebook.com/v21.0"
INSTAGRAM = "https://graph.instagram.com/v21.0"
FILE_LIMIT = 25 << 20
BAD_TOKEN_CODES = {190}  # Meta's "the access token is no longer valid"


class MetaError(RuntimeError):
    def __init__(self, description, code=0):
        super().__init__(description)
        self.code = code


def call(base: str, token: str, method: str, path: str, body=None, files=None, timeout=20):
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if files:
        boundary = uuid.uuid4().hex
        out = bytearray()
        for name, value in (body or {}).items():
            out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, (file_name, content, kind) in files.items():
            out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{file_name}"\r\n'
                    f"Content-Type: {kind}\r\n\r\n").encode() + content + b"\r\n"
        out += f"--{boundary}--\r\n".encode()
        data, headers["Content-Type"] = bytes(out), f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            raw = reply.read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read() or b"{}").get("error") or {}
        except ValueError:
            error = {}
        finally:
            exc.close()
        raise MetaError(str(error.get("message") or f"Meta answered {exc.code}"), int(error.get("code") or exc.code)) from None


def signed(body: bytes, header: str, app_secret: str) -> bool:
    # Whether Meta signed this delivery with the app's secret.
    expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(app_secret) and hmac.compare_digest(expected, str(header or ""))


class MetaWebhook:
    # What both apps share: Meta's check of the link, signed deliveries, and handling them off the server's thread.
    def __init__(self, token: str, app_secret: str, verify_token, link):
        self.token, self.app_secret, self.verify_token, self.link = token, app_secret, verify_token, link
        link.transport = self

    def start(self):
        pass  # Meta sends the messages: nothing to keep running

    def stop(self):
        pass

    def verify(self, query: dict):
        # Meta's check of the link when it is set up: the challenge back when the verify token is right.
        expected = self.verify_token() if callable(self.verify_token) else self.verify_token
        if query.get("hub.mode") == "subscribe" and expected and hmac.compare_digest(str(query.get("hub.verify_token", "")), expected):
            return str(query.get("hub.challenge", ""))
        return None

    def deliver(self, body: bytes, signature: str) -> bool:
        # A delivery from Meta: handled in the background when it's signed (Meta wants its answer at once).
        if not signed(body, signature, self.app_secret):
            LOG.warning("%s: a delivery without a good signature was ignored", self.link.kind)
            return False
        try:
            payload = json.loads(body)
        except ValueError:
            return False
        threading.Thread(target=self._safely, args=(payload,), daemon=True).start()
        return True

    def _safely(self, payload):
        try:
            self.read(payload)
        except Exception:
            LOG.exception("A %s message failed", self.link.kind)

    def _download(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(request, timeout=60) as reply:
            data = reply.read(FILE_LIMIT + 1)
        if len(data) > FILE_LIMIT:
            raise MetaError("too big")
        return data

    def chat_for(self, owner: str) -> str:
        return owner

    def _call(self, *args, **kwargs):
        # A call to Meta's API that also says when the access token ran out (a day's token does).
        try:
            return call(*args, **kwargs)
        except MetaError as exc:
            if exc.code in BAD_TOKEN_CODES:
                self.link.helper.changed("bad key", "Meta's access token ran out. Paste a new one (New key).")
            raise


class WhatsAppLink(MetaWebhook):
    def __init__(self, token: str, phone_id: str, app_secret: str, verify_token, link):
        super().__init__(token, app_secret, verify_token, link)
        self.phone_id = phone_id

    def read(self, payload: dict):
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "") for c in value.get("contacts") or []}
                for message in value.get("messages") or []:
                    self.read_message(message, names.get(message.get("from"), ""))

    def read_message(self, message: dict, name: str):
        sender, kind = str(message.get("from") or ""), message.get("type")
        text, sound, files = "", None, []
        if kind == "text":
            text = (message.get("text") or {}).get("body", "")
        elif kind == "button":
            text = (message.get("button") or {}).get("text", "")
        elif kind in ("audio", "image", "document", "video", "sticker"):
            media = message.get(kind) or {}
            fetch = lambda media_id=media.get("id"): self.media(media_id)
            if kind == "audio":
                sound = (fetch, str(media.get("mime_type") or "audio/ogg").split(";")[0])
            else:
                extension = {"image": ".jpg", "video": ".mp4", "sticker": ".webp"}.get(kind, "")
                files.append((media.get("filename") or f"WhatsApp {kind}{extension}", fetch))
                text = media.get("caption", "") if kind != "document" else ""
        try:
            sent = float(message.get("timestamp"))
        except (TypeError, ValueError):
            sent = None
        self.link.receive(sender, sender, text, name, sent, sound, files)

    def media(self, media_id: str) -> bytes:
        info = self._call(GRAPH, self.token, "GET", f"/{media_id}")
        return self._download(info["url"])

    def send(self, chat, text: str):
        for start in range(0, len(text), 4000):
            self._call(GRAPH, self.token, "POST", f"/{self.phone_id}/messages",
                 {"messaging_product": "whatsapp", "to": chat, "type": "text", "text": {"body": text[start:start + 4000]}})

    def send_photo(self, chat, jpeg: bytes):
        uploaded = self._call(GRAPH, self.token, "POST", f"/{self.phone_id}/media", {"messaging_product": "whatsapp", "type": "image/jpeg"},
                        files={"file": ("screen.jpg", jpeg, "image/jpeg")}, timeout=60)
        self._call(GRAPH, self.token, "POST", f"/{self.phone_id}/messages",
             {"messaging_product": "whatsapp", "to": chat, "type": "image", "image": {"id": uploaded["id"]}})


class InstagramLink(MetaWebhook):
    def read(self, payload: dict):
        for entry in payload.get("entry") or []:
            for event in entry.get("messaging") or []:
                message = event.get("message") or {}
                if message.get("is_echo") or not message:
                    continue  # its own replies, and reactions or reads
                sender = str((event.get("sender") or {}).get("id") or "")
                sound, files = None, []
                for item in message.get("attachments") or []:
                    url = (item.get("payload") or {}).get("url")
                    if not url:
                        continue
                    fetch = lambda u=url: self._download(u)
                    if item.get("type") == "audio":
                        sound = (fetch, "audio/mp4")
                    else:
                        files.append((f"Instagram {item.get('type', 'file')}" + {"image": ".jpg", "video": ".mp4"}.get(item.get("type"), ""), fetch))
                try:
                    sent = float(event.get("timestamp")) / 1000
                except (TypeError, ValueError):
                    sent = None
                self.link.receive(sender, sender, message.get("text", ""), "", sent, sound, files)

    def send(self, chat, text: str):
        for start in range(0, len(text), 1000):
            self._call(INSTAGRAM, self.token, "POST", "/me/messages", {"recipient": {"id": chat}, "message": {"text": text[start:start + 1000]}})


def check_whatsapp(token: str, phone_id: str) -> dict:
    info = call(GRAPH, token, "GET", f"/{phone_id}?fields=display_phone_number,verified_name")
    return {"name": str(info.get("verified_name") or info.get("display_phone_number") or ""),
            "number": "".join(ch for ch in str(info.get("display_phone_number") or "") if ch.isdigit())}


def check_instagram(token: str) -> dict:
    info = call(INSTAGRAM, token, "GET", "/me?fields=user_id,username")
    return {"name": "@" + str(info.get("username") or ""), "username": str(info.get("username") or "")}
