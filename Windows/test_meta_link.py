import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import hashlib
import hmac
import http.client
import http.server
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import chat_link
import meta_link
import motorcycle_man as app
from test_chat_link import FakeHelper, wait_for

SECRET, VERIFY, TOKEN, OWNER = "app-secret", "verify-me", "meta-token", "15551234567"


def signature(body: bytes, secret=SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class FakeGraph:
    # Meta's Graph API on 127.0.0.1: records what is sent, serves media, and refuses a stale token.
    def __init__(self):
        self.posted, self.files, self.expired = [], {"/files/voice.ogg": b"OggS voice"}, False
        fake = self

        class Graph(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, data):
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self.reply(401, {"error": {"message": "bad", "code": 190}})
                if self.path in fake.files:
                    data = fake.files[self.path]
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    return self.wfile.write(data)
                if self.path.startswith("/media-1"):
                    return self.reply(200, {"url": fake.address + "/files/voice.ogg"})
                self.reply(200, {"verified_name": "Bike PC", "display_phone_number": "+1 555-000-1111",
                                 "username": "bike.pc", "user_id": "9"})

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if fake.expired:
                    return self.reply(400, {"error": {"message": "Session has expired", "code": 190}})
                fake.posted.append((self.path, json.loads(body)))
                self.reply(200, {"messages": [{"id": "wamid.1"}]})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Graph)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.address = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def whatsapp_delivery(message: dict) -> bytes:
    return json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": message["from"], "profile": {"name": "Mani"}}], "messages": [message]}}]}]}).encode()


class MetaTests(unittest.TestCase):
    def setUp(self):
        self.graph = FakeGraph()
        self.addCleanup(self.graph.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (mock.patch.object(meta_link, "GRAPH", self.graph.address), mock.patch.object(meta_link, "INSTAGRAM", self.graph.address),
                        mock.patch.dict(os.environ, {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1", "APPDATA": self.temp.name})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.helper = FakeHelper()
        self.whatsapp = meta_link.WhatsAppLink(TOKEN, "phone-1", SECRET, lambda: VERIFY, chat_link.ChatLink("whatsapp", self.helper, OWNER, slash=False))
        self.instagram = meta_link.InstagramLink(TOKEN, SECRET, VERIFY, chat_link.ChatLink("instagram", self.helper, "ig-owner", slash=False))
        services = {"whatsapp": mock.Mock(transport=self.whatsapp), "instagram": mock.Mock(transport=self.instagram)}
        patcher = mock.patch.dict(app.CHAT_SERVICES, services, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(mock.Mock(), 1000, 800))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request(method, path, body=body, headers=headers or {})
        reply = connection.getresponse()
        data = reply.read()
        connection.close()
        return reply.status, data

    def sent_texts(self, path):
        return [body for p, body in self.graph.posted if p == path]

    def test_meta_checks_the_link_with_the_verify_token(self):
        status, body = self.request("GET", f"/whatsapp?hub.mode=subscribe&hub.verify_token={VERIFY}&hub.challenge=12345")
        self.assertEqual((status, body), (200, b"12345"))
        self.assertEqual(self.request("GET", "/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=1")[0], 403)
        self.assertEqual(self.request("GET", f"/instagram?hub.mode=subscribe&hub.verify_token={VERIFY}&hub.challenge=7")[1], b"7")

    def test_whatsapp_messages_are_signed_commands_answered_on_whatsapp(self):
        body = whatsapp_delivery({"from": OWNER, "type": "text", "timestamp": str(int(time.time())), "text": {"body": "pause the music"}})
        self.assertEqual(self.request("POST", "/whatsapp", body, {"X-Hub-Signature-256": signature(body, "not-the-secret")})[0], 403)
        time.sleep(0.1)
        self.assertEqual(self.helper.ran, [])  # someone else's delivery does nothing
        self.assertEqual(self.request("POST", "/whatsapp", body, {"X-Hub-Signature-256": signature(body)})[0], 200)
        self.assertTrue(wait_for(lambda: any(b["text"]["body"] == "Done: pause the music" for b in self.sent_texts("/phone-1/messages"))))
        self.assertEqual(self.sent_texts("/phone-1/messages")[-1]["to"], OWNER)
        voice = whatsapp_delivery({"from": OWNER, "type": "audio", "timestamp": str(int(time.time())),
                                   "audio": {"id": "media-1", "mime_type": "audio/ogg; codecs=opus"}})
        self.request("POST", "/whatsapp", voice, {"X-Hub-Signature-256": signature(voice)})
        self.assertTrue(wait_for(lambda: self.helper.heard == [(b"OggS voice", "audio/ogg")]))

    def test_instagram_messages_and_its_own_echoes(self):
        def delivery(message):
            return json.dumps({"object": "instagram", "entry": [{"messaging": [
                {"sender": {"id": "ig-owner"}, "timestamp": int(time.time() * 1000), "message": message}]}]}).encode()
        echo = delivery({"mid": "1", "text": "Done: earlier", "is_echo": True})
        text = delivery({"mid": "2", "text": "open youtube"})
        for body in (echo, text):
            self.assertEqual(self.request("POST", "/instagram", body, {"X-Hub-Signature-256": signature(body)})[0], 200)
        self.assertTrue(wait_for(lambda: any(b["message"]["text"] == "Done: open youtube" for b in self.sent_texts("/me/messages"))))
        self.assertEqual(self.helper.ran, ["open youtube"])
        self.instagram.link.receive("ig-owner", "ig-owner", "screen")  # no pictures through Instagram
        self.assertIn("don't come through here", self.sent_texts("/me/messages")[-1]["message"]["text"])

    def test_a_token_that_ran_out_is_said(self):
        self.graph.expired = True
        with self.assertRaises(meta_link.MetaError):
            self.whatsapp.send(OWNER, "hi")
        self.assertEqual(self.helper.states[-1][0], "bad key")

    def test_checking_keys_and_the_fixed_link_first(self):
        self.assertEqual(meta_link.check_whatsapp(TOKEN, "phone-1"), {"name": "Bike PC", "number": "15550001111"})
        self.assertEqual(meta_link.check_instagram(TOKEN)["username"], "bike.pc")
        whatsapp = next(spec for spec in app.CHAT_SPECS if spec["kind"] == "whatsapp")
        with self.assertRaises(app.SetupNeeded):
            whatsapp["check"]({"token": TOKEN, "phone_id": "phone-1", "app_secret": SECRET})
        app.save_setting("ngrok_domain", "bike-example.ngrok-free.app")
        app.save_key("ngrok_authtoken", "x" * 30)
        info = whatsapp["check"]({"token": TOKEN, "phone_id": "phone-1", "app_secret": SECRET})
        self.assertGreaterEqual(len(info["verify"]), 20)
        self.assertIn("https://bike-example.ngrok-free.app/whatsapp", whatsapp["pair_html"](info, "123456"))
        self.assertEqual(whatsapp["pair_link"](info, "123456"), "https://wa.me/15550001111?text=123456")
        self.assertTrue(meta_link.signed(b"x", signature(b"x"), SECRET))
        self.assertFalse(meta_link.signed(b"x", signature(b"x"), ""))


if __name__ == "__main__":
    unittest.main()
