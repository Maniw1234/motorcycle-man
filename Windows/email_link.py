# Motorcycle Man by email: the PC gets an email address (a free Gmail works; its "app password" lets Motorcycle
# Man read and send its mail), and emails from the linked address are commands, answered by email. A From line
# can be forged, so only mail the receiving mail service vouched for (DKIM or DMARC passing in its
# Authentication-Results) counts. The password is a secret: it is never logged or shown.
import email, email.message, email.policy, email.utils, imaplib, logging, re, smtplib, ssl, threading

LOG = logging.getLogger("motorcycle_man")
POLL_SECONDS = 20
SERVERS = {  # the providers whose app passwords work for IMAP and SMTP
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com"), "googlemail.com": ("imap.gmail.com", "smtp.gmail.com"),
    "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"), "icloud.com": ("imap.mail.me.com", "smtp.mail.me.com"),
    "me.com": ("imap.mail.me.com", "smtp.mail.me.com"), "aol.com": ("imap.aol.com", "smtp.aol.com"),
}
OUR_MARK = "X-Motorcycle-Man"  # on every email it sends, so its own replies are never read as commands
AUDIO = re.compile(r"\.(m4a|ogg|oga|opus|mp3|wav|aac|amr|3gp)$", re.IGNORECASE)


class EmailError(RuntimeError):
    pass


def servers(address: str):
    domain = address.rsplit("@", 1)[-1].lower()
    return SERVERS.get(domain, (f"imap.{domain}", f"smtp.{domain}"))


def clean_address(text) -> str:
    return email.utils.parseaddr(str(text))[1].strip().lower()


def check(address: str, password: str) -> dict:
    # Signs in once, to see the address and app password work.
    address = clean_address(address)
    if "@" not in address:
        raise EmailError("that isn't an email address")
    try:
        imap = imaplib.IMAP4_SSL(servers(address)[0], timeout=20)
        try:
            imap.login(address, password.replace(" ", ""))
        finally:
            try:
                imap.logout()
            except Exception:
                pass
    except imaplib.IMAP4.error:
        raise EmailError("the address or app password was refused") from None
    return {"name": address}


def vouched_for(message, sender: str, address: str) -> bool:
    # Whether the receiving mail service vouched that sender really sent it: its Authentication-Results (the top
    # one, which it added itself) has DMARC passing, or DKIM passing for the sender's own domain. Mail the
    # account sent to itself never passed through that check, and has no such line.
    results = message.get_all("Authentication-Results") or []
    domain = sender.rsplit("@", 1)[-1].lower()
    if not results:
        return sender == address
    top = str(results[0]).lower()
    if re.search(r"\bdmarc=pass\b[^;]*header\.from=" + re.escape(domain) + r"\b", top):
        return True
    return any(re.search(r"header\.(?:i|d)=@?(?:[\w.-]+\.)?" + re.escape(domain) + r"\b", part)
               for part in top.split(";") if re.search(r"\bdkim=pass\b", part))


def words_of(message, subject: bool = True) -> str:
    # What the email says: its own words without the quoted reply below them or a signature, else its subject.
    body = message.get_body(preferencelist=("plain",))
    text = body.get_content() if body is not None else ""
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith(">") or re.match(r"^\s*(On .+ wrote:|-- ?$|Sent from my )", line):
            break
        lines.append(line)
    words = " ".join(" ".join(lines).split())
    if not words and subject:
        words = re.sub(r"^(?:(?:re|fwd?|aw)\s*:\s*)+", "", str(message.get("Subject") or ""), flags=re.IGNORECASE).strip()
    return words[:2000]


class EmailLink:
    def __init__(self, address: str, password: str, link):
        self.address, self.password, self.link = clean_address(address), password.replace(" ", ""), link
        self.imap_host, self.smtp_host = servers(self.address)
        link.transport = self
        self.threads = {}  # sender -> (the Message-ID to answer, its subject), so replies stay in one thread
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name="email", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()

    def run(self):
        delay, failed, seen = 5, False, None
        while not self.stopped.is_set():
            try:
                imap = imaplib.IMAP4_SSL(self.imap_host, timeout=30)
                try:
                    imap.login(self.address, self.password)
                    imap.select("INBOX")
                    if seen is None:  # only mail that arrives from now on
                        seen = max([int(uid) for uid in imap.uid("search", None, "ALL")[1][0].split()] or [0])
                        self.link.helper.changed("on", self.address)
                    elif failed:
                        self.link.helper.changed("on", self.address)
                    delay, failed = 5, False
                    while not self.stopped.is_set():
                        found = imap.uid("search", None, f"UID {seen + 1}:*")[1][0].split()
                        for uid in sorted(int(u) for u in found if int(u) > seen):
                            seen = uid
                            data = imap.uid("fetch", str(uid), "(RFC822)")[1]
                            raw = next((part[1] for part in data if isinstance(part, tuple)), b"")
                            try:
                                self.read(raw)
                            except Exception:
                                LOG.exception("An email failed")
                        self.stopped.wait(POLL_SECONDS)
                        if not self.stopped.is_set():
                            imap.noop()
                finally:
                    try:
                        imap.logout()
                    except Exception:
                        pass
            except imaplib.IMAP4.error as exc:
                if "auth" in str(exc).lower() or "login" in str(exc).lower() or "credentials" in str(exc).lower():
                    self.link.helper.changed("bad key", "The email address or app password was refused. Set it up again.")
                    return
                failed = self._wait(failed, delay)
                delay = min(delay * 2, 120)
            except (OSError, ssl.SSLError, EOFError):
                failed = self._wait(failed, delay)
                delay = min(delay * 2, 120)

    def _wait(self, failed: bool, delay: float) -> bool:
        if not self.stopped.is_set():
            if not failed:
                LOG.warning("Email: can't reach the mail server, trying again")
                self.link.helper.changed("failed", "Can't reach the mail server. Trying again...")
            self.stopped.wait(delay)
        return True

    def read(self, raw: bytes):
        message = email.message_from_bytes(raw, policy=email.policy.default)
        if message.get(OUR_MARK):
            return  # one of its own replies
        name, sender = email.utils.parseaddr(str(message.get("From") or ""))
        sender = sender.lower()
        if not sender or not vouched_for(message, sender, self.address):
            LOG.info("Email: ignored one its mail service didn't vouch for")
            return
        sound, files = None, []
        for part in message.iter_attachments():
            file_name = part.get_filename() or "attachment"
            data = part.get_content() if isinstance(part.get_content(), bytes) else part.get_content().encode()
            if AUDIO.search(file_name) or part.get_content_type().startswith("audio/"):
                sound = (data, part.get_content_type() if part.get_content_type().startswith("audio/") else "audio/mp4")
            else:
                files.append((file_name, data))
        try:
            sent = email.utils.parsedate_to_datetime(str(message.get("Date"))).timestamp()
        except (TypeError, ValueError):
            sent = None
        self.threads[sender] = (str(message.get("Message-ID") or ""), str(message.get("Subject") or "Motorcycle Man"))
        # An email of only photos or files: its subject is their title, not a command.
        self.link.receive(sender, sender, words_of(message, subject=not (files or sound)), name, sent, sound, files)

    # --- what goes out ---

    def _message(self, chat: str) -> email.message.EmailMessage:
        answering, subject = self.threads.get(chat, ("", "Motorcycle Man"))
        message = email.message.EmailMessage()
        message["From"] = email.utils.formataddr(("Motorcycle Man", self.address))
        message["To"] = chat
        message["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        message[OUR_MARK] = "1"
        if answering:
            message["In-Reply-To"] = message["References"] = answering
        return message

    def _send(self, message):
        with smtplib.SMTP_SSL(self.smtp_host, 465, timeout=30) as smtp:
            smtp.login(self.address, self.password)
            smtp.send_message(message)

    def send(self, chat, text: str):
        message = self._message(chat)
        message.set_content(text)
        self._send(message)

    def send_photo(self, chat, jpeg: bytes):
        message = self._message(chat)
        message.set_content("Here's the PC's screen.")
        message.add_attachment(jpeg, maintype="image", subtype="jpeg", filename="screen.jpg")
        self._send(message)

    def chat_for(self, owner: str) -> str:
        return owner
