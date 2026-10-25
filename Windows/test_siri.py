import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.client
import http.server
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
from unittest import mock

import motorcycle_man as app

TOKEN = "siri-test-token"


class SiriAndIMessageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (mock.patch.dict(os.environ, {"APPDATA": self.temp.name}),
                        mock.patch.object(app, "PHONE_SESSION_TOKEN", TOKEN), mock.patch.dict(app.PHONE_CONTACT)):
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

    def test_a_shortcut_asks_by_json_form_or_link(self):
        with mock.patch.object(app, "execute_smart_action", side_effect=lambda text, *a, **k: f"Did: {text}") as run:
            status, body = self.request("POST", f"/ask?token={TOKEN}", json.dumps({"text": "open gmail"}),
                                        {"Content-Type": "application/json"})
            self.assertEqual((status, json.loads(body)), (200, {"reply": "Did: open gmail"}))
            status, body = self.request("POST", f"/ask?token={TOKEN}", "text=MM+pause+the+music",
                                        {"Content-Type": "application/x-www-form-urlencoded"})
            self.assertEqual(json.loads(body), {"reply": "Did: pause the music"})  # a text message's "MM" is dropped
            status, body = self.request("GET", f"/ask?text={urllib.parse.quote('lock the PC')}&token={TOKEN}")
            self.assertEqual(json.loads(body), {"reply": "Did: lock the PC"})
            status, body = self.request("POST", f"/ask?token={TOKEN}", "{}", {"Content-Type": "application/json"})
            self.assertEqual(json.loads(body), {"reply": "Say what to do."})
        self.assertEqual(run.call_count, 3)
        status, _ = self.request("POST", "/ask", json.dumps({"text": "open gmail"}))
        self.assertEqual(status, 403)  # never without the link's secret

    def test_the_guide_has_the_right_link(self):
        status, body = self.request("GET", f"/shortcut?token={TOKEN}", headers={"Host": "192.168.1.20:8080"})
        page = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn(f"http://192.168.1.20:8080/ask?token={TOKEN}", page)
        self.assertIn("only works on your home Wi-Fi", page)
        _, body = self.request("GET", f"/shortcut?token={TOKEN}", headers={"Host": "words-here.trycloudflare.com"})
        self.assertIn(f"https://words-here.trycloudflare.com/ask?token={TOKEN}", body.decode("utf-8"))
        app.save_setting("ngrok_domain", "bike-example.ngrok-free.app")
        app.save_key("ngrok_authtoken", "x" * 30)
        _, body = self.request("GET", f"/shortcut?token={TOKEN}", headers={"Host": "192.168.1.20:8080"})
        page = body.decode("utf-8")
        self.assertIn(f"https://bike-example.ngrok-free.app/ask?token={TOKEN}", page)  # the fixed link wins
        self.assertNotIn("only works on your home Wi-Fi", page)
        self.assertEqual(self.request("GET", "/shortcut")[0], 403)
        self.assertIn("openSiriGuide", app.PHONE_PAGE)


if __name__ == "__main__":
    unittest.main()
