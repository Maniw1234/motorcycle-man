import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import email
import email.message
import email.policy
import email.utils
import imaplib
import threading
import time
import unittest
from unittest import mock

import chat_link
import email_link
from test_chat_link import FakeHelper, wait_for

PC, OWNER = "pc.example.mm@gmail.com", "owner.example.mm@gmail.com"
VOUCHED = ("mx.google.com; dkim=pass header.i=@gmail.com header.s=20230601; spf=pass smtp.mailfrom=owner.example.mm@gmail.com; "
           "dmarc=pass (p=NONE sp=QUARANTINE dis=NONE) header.from=gmail.com")


def mail(body="", subject="Hi", sender=OWNER, results=VOUCHED, attachments=(), extra=None):
    message = email.message.EmailMessage()
    message["From"] = email.utils.formataddr(("Mani", sender))
    message["To"] = PC
    message["Subject"] = subject
    message["Date"] = email.utils.formatdate(localtime=True)
    message["Message-ID"] = email.utils.make_msgid()
    if results:  # the receiving mail service's own line, on top
        message["Authentication-Results"] = results
    for header, value in (extra or {}).items():
        message[header] = value
    message.set_content(body)
    for name, data, kind in attachments:
        main, sub = kind.split("/")
        message.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return message.as_bytes()


class FakeMailServer:
    # An IMAP inbox and an SMTP outbox, standing in for Gmail.
    def __init__(self, password="right-password"):
        self.password, self.inbox, self.sent, self.lock = password, {}, [], threading.Lock()
        server = self

        class IMAP:
            def __init__(self, host, timeout=None):
                self.host = host

            def login(self, user, password):
                if (user, password) != (PC, server.password):
                    raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

            def select(self, box):
                return "OK", [b"1"]

            def uid(self, command, *args):
                with server.lock:
                    uids = sorted(server.inbox)
                if command == "search":
                    if args[1] != "ALL":
                        start = int(args[1].split()[1].split(":")[0])
                        uids = [u for u in uids if u >= start] or uids[-1:]  # IMAP always answers with the newest
                    return "OK", [" ".join(map(str, uids)).encode()]
                raw = server.inbox[int(args[0])]
                return "OK", [(b"%s (RFC822 {%d}" % (args[0].encode(), len(raw)), raw), b")"]

            def noop(self):
                return "OK", []

            def logout(self):
                pass

        class SMTP:
            def __init__(self, host, port, timeout=None):
                self.host = host

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def login(self, user, password):
                if (user, password) != (PC, server.password):
                    raise email_link.smtplib.SMTPAuthenticationError(535, b"bad")

            def send_message(self, message):
                server.sent.append(message)

        self.imap, self.smtp = IMAP, SMTP

    def deliver(self, raw):
        with self.lock:
            self.inbox[max(self.inbox, default=0) + 1] = raw


class EmailCheckTests(unittest.TestCase):
    def test_only_mail_its_service_vouched_for(self):
        parse = lambda raw: email.message_from_bytes(raw, policy=email.policy.default)
        self.assertTrue(email_link.vouched_for(parse(mail()), OWNER, PC))
        forged = "mx.google.com; dkim=none; spf=fail smtp.mailfrom=evil.example; dmarc=fail (p=NONE) header.from=gmail.com"
        self.assertFalse(email_link.vouched_for(parse(mail(results=forged)), OWNER, PC))
        # a forger's own "pass" line sits under the one the receiving service added
        both = parse(mail(results=forged, extra={"Authentication-Results": VOUCHED}))
        self.assertFalse(email_link.vouched_for(both, OWNER, PC))
        self.assertTrue(email_link.vouched_for(parse(mail(results="")), PC, PC))  # sent to itself
        self.assertFalse(email_link.vouched_for(parse(mail(results="")), OWNER, PC))
        dkim_only = "mx.google.com; dkim=pass header.i=@gmail.com header.s=x; spf=softfail"
        self.assertTrue(email_link.vouched_for(parse(mail(results=dkim_only)), OWNER, PC))

    def test_what_an_email_says(self):
        parse = lambda raw: email.message_from_bytes(raw, policy=email.policy.default)
        body = "open gmail please\n\n-- \nMani\n\nOn Mon, Motorcycle Man wrote:\n> Done."
        self.assertEqual(email_link.words_of(parse(mail(body))), "open gmail please")
        self.assertEqual(email_link.words_of(parse(mail("", subject="Re: Fwd: pause the music"))), "pause the music")
        self.assertEqual(email_link.servers("x@icloud.com"), ("imap.mail.me.com", "smtp.mail.me.com"))
        self.assertEqual(email_link.servers("x@example.org"), ("imap.example.org", "smtp.example.org"))


class EmailLinkTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeMailServer()
        for patcher in (mock.patch.object(email_link.imaplib, "IMAP4_SSL", self.server.imap),
                        mock.patch.object(email_link.smtplib, "SMTP_SSL", self.server.smtp),
                        mock.patch.object(email_link, "POLL_SECONDS", 0.05)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.helper = FakeHelper()

    def start(self, password="right-password"):
        link = chat_link.ChatLink("email", self.helper, OWNER, slash=False)
        transport = email_link.EmailLink(PC, password, link)
        transport.start()
        self.addCleanup(transport.stop)
        return transport

    def replies(self):
        return [m.get_body(preferencelist=("plain",)).get_content().strip() for m in self.server.sent]

    def test_checking_the_address_and_password(self):
        self.assertEqual(email_link.check(f"Mani PC <{PC}>", "right-password"), {"name": PC})
        with self.assertRaises(email_link.EmailError):
            email_link.check(PC, "wrong")
        with self.assertRaises(email_link.EmailError):
            email_link.check("not an address", "x")

    def test_emails_are_commands_answered_by_email(self):
        self.server.deliver(mail("delete everything"))  # there before it started: left alone
        self.start()
        self.assertTrue(wait_for(lambda: ("on", PC) in self.helper.states))
        self.server.deliver(mail("pause the music", subject="Music"))
        self.assertTrue(wait_for(lambda: "Done: pause the music" in self.replies()))
        reply = self.server.sent[-1]
        self.assertEqual((reply["To"], reply["Subject"], reply[email_link.OUR_MARK]), (OWNER, "Re: Music", "1"))
        self.assertTrue(reply["In-Reply-To"])
        self.server.deliver(reply.as_bytes())  # its own reply, back in the inbox (sent to itself)
        forged = "mx.google.com; spf=fail; dmarc=fail (p=NONE) header.from=gmail.com"
        self.server.deliver(mail("delete my files", results=forged))
        self.server.deliver(mail("", attachments=[("notes.pdf", b"%PDF", "application/pdf")]))
        self.assertTrue(wait_for(lambda: self.helper.saved == [("notes.pdf", b"%PDF")]))
        self.server.deliver(mail("", attachments=[("memo.m4a", b"audio", "audio/mp4")]))
        self.assertTrue(wait_for(lambda: any("Done: open notepad" in r for r in self.replies())))
        self.assertEqual(self.helper.heard, [(b"audio", "audio/mp4")])
        time.sleep(0.2)
        self.assertEqual(self.helper.ran, ["pause the music", "open notepad"])  # nothing else ran

    def test_a_refused_password_stops_it(self):
        transport = self.start("wrong")
        transport.thread.join(5)
        self.assertFalse(transport.thread.is_alive())
        self.assertEqual(self.helper.states[-1][0], "bad key")


if __name__ == "__main__":
    unittest.main()
