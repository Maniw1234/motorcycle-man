import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.server
import json
import os
import queue
import threading
import time
import unittest
from unittest import mock

from websockets.sync.server import serve

import chat_link
import discord_bot
from test_chat_link import FakeHelper, wait_for

TOKEN = "discord-test-key"
OWNER = "1001"


class FakeDiscord:
    # Discord's gateway (a websocket) and REST API on 127.0.0.1, with events a test queues.
    def __init__(self):
        self.events, self.posted, self.files = queue.Queue(), [], {"/files/voice.ogg": b"OggS voice", "/files/notes.pdf": b"%PDF"}
        fake = self

        def gateway(ws):
            ws.send(json.dumps({"op": 10, "d": {"heartbeat_interval": 45000}}))
            hello = json.loads(ws.recv(timeout=5))
            if hello.get("d", {}).get("token") != TOKEN:
                ws.close(4004, "Authentication failed")
                return
            ws.send(json.dumps({"op": 0, "t": "READY", "s": 1, "d": {"user": {"username": "bike_bot", "id": "42"}}}))
            while True:
                try:
                    event = fake.events.get(timeout=0.1)
                except queue.Empty:
                    try:
                        ws.recv(timeout=0.01)  # heartbeats, or the bot going away
                    except TimeoutError:
                        continue
                    except Exception:
                        return
                    continue
                ws.send(json.dumps(event))

        self.ws = serve(gateway, "127.0.0.1", 0)
        threading.Thread(target=self.ws.serve_forever, daemon=True).start()
        self.gateway = f"ws://127.0.0.1:{self.ws.socket.getsockname()[1]}"

        class Rest(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, data=None):
                raw = json.dumps(data).encode() if data is not None else b""
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path in fake.files:
                    data = fake.files[self.path]
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif self.headers.get("Authorization") != f"Bot {TOKEN}":
                    self.reply(401, {"message": "401: Unauthorized"})
                else:
                    self.reply(200, {"username": "bike_bot", "id": "42"})

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                fake.posted.append((self.path, body))
                if self.path == "/users/@me/channels":
                    self.reply(200, {"id": "dm-1"})
                elif self.path.endswith("/typing"):
                    self.reply(204)
                else:
                    self.reply(200, {"id": "m"})

        self.rest = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Rest)
        threading.Thread(target=self.rest.serve_forever, daemon=True).start()
        self.api = f"http://127.0.0.1:{self.rest.server_address[1]}"

    def message(self, content="", author=OWNER, channel="c-1", guild=None, bot=False, attachments=(), flags=0):
        data = {"channel_id": channel, "content": content, "author": {"id": author, "username": "mani", "bot": bot},
                "attachments": list(attachments), "flags": flags, "timestamp": "2099-01-01T00:00:00+00:00"}
        if guild:
            data["guild_id"] = guild
        self.events.put({"op": 0, "t": "MESSAGE_CREATE", "s": 2, "d": data})

    def texts(self, channel="c-1"):
        return [json.loads(body)["content"] for path, body in self.posted if path == f"/channels/{channel}/messages"]

    def close(self):
        self.ws.shutdown()
        self.rest.shutdown()
        self.rest.server_close()


class DiscordBotTests(unittest.TestCase):
    def setUp(self):
        self.discord = FakeDiscord()
        self.addCleanup(self.discord.close)
        for patcher in (mock.patch.object(discord_bot, "API", self.discord.api),
                        mock.patch.object(discord_bot, "GATEWAY", self.discord.gateway),
                        mock.patch.dict(os.environ, {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.helper = FakeHelper()

    def start(self, token=TOKEN):
        link = chat_link.ChatLink("discord", self.helper, OWNER)
        bot = discord_bot.DiscordBot(token, link)
        bot.start()
        self.addCleanup(bot.stop)
        return bot

    def test_checking_a_key(self):
        self.assertEqual(discord_bot.check_key(TOKEN), ("bike_bot", "42"))
        with self.assertRaises(discord_bot.DiscordError) as refused:
            discord_bot.check_key("wrong")
        self.assertEqual(refused.exception.code, 401)
        self.assertIn("client_id=42", discord_bot.invite_link("42"))

    def test_direct_messages_are_commands_and_others_are_ignored(self):
        bot = self.start()
        self.assertTrue(wait_for(lambda: ("on", "bike_bot") in self.helper.states))
        self.discord.message("pause the music", guild="server-1")  # in a server: not for it
        self.discord.message("pause the music", bot=True)  # another bot
        self.discord.message("pause the music")
        self.assertTrue(wait_for(lambda: "Done: pause the music" in self.discord.texts()))
        self.assertEqual(self.helper.ran, ["pause the music"])
        self.assertTrue(wait_for(lambda: ("/channels/c-1/typing", b"") in self.discord.posted))  # "typing..." meanwhile
        voice = {"url": self.discord.api + "/files/voice.ogg", "filename": "voice-message.ogg", "size": 10,
                 "content_type": "audio/ogg"}
        self.discord.message(attachments=[voice], flags=discord_bot.VOICE_MESSAGE)
        self.assertTrue(wait_for(lambda: any("Done: open notepad" in t for t in self.discord.texts())))
        self.assertEqual(self.helper.heard, [(b"OggS voice", "audio/ogg")])
        pdf = {"url": self.discord.api + "/files/notes.pdf", "filename": "notes.pdf", "size": 4}
        self.discord.message(attachments=[pdf])
        self.assertTrue(wait_for(lambda: self.helper.saved == [("notes.pdf", b"%PDF")]))
        self.assertEqual(bot.chat_for(OWNER), "dm-1")

    def test_a_wrong_key_stops_it(self):
        bot = self.start("wrong")
        bot.thread.join(5)
        self.assertFalse(bot.thread.is_alive())
        self.assertEqual(self.helper.states[-1][0], "bad key")


if __name__ == "__main__":
    unittest.main()
