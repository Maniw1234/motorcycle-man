import mm_test_env  # noqa: F401 (first: the tests never touch the real settings and keys)
import http.server
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

import bike_rider
import helmet_face
import motorcycle_man as app
import paint_jobs


class PaintJobTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(paint_jobs.set_current, paint_jobs.DEFAULT)

    def test_names_for_paint_jobs(self):
        for words, name in (("green", "lime"), ("Kawasaki", "lime"), ("racing red", "red"), ("the white one", "white"),
                            ("black", "midnight"), ("electric blue", "blue"), ("gold flake", "gold")):
            self.assertEqual(paint_jobs.find(words, strict=True), name, words)
        self.assertIsNone(paint_jobs.find("a picture of a red car", strict=True))
        self.assertEqual(paint_jobs.find("a picture of a red car"), "red")  # loosely, it's there
        self.assertFalse(paint_jobs.set_current("purple"))
        self.assertEqual(paint_jobs.current(), paint_jobs.DEFAULT)

    def test_the_helmet_and_the_bodywork_take_the_paint_but_the_frame_stays_black(self):
        black_face, black_bike = helmet_face.face_svg("idle"), bike_rider.bike_svg()
        self.assertIn('stop-color="#3C414E"', black_face)  # the original shell
        self.assertTrue(paint_jobs.set_current("lime"))
        green_face, green_bike = helmet_face.face_svg("idle"), bike_rider.bike_svg()
        self.assertIn('stop-color="#D2F77A"', green_face)
        self.assertNotIn('stop-color="#3C414E"', green_face.split('id="hm-panel"')[0])
        self.assertIn('stop-color="#B4EE5A"', green_bike)
        self.assertNotEqual(black_bike, green_bike)
        self.assertEqual(green_bike.count('fill="url(#rd-dark)"'), 3)  # the swingarm, the frame and the muffler
        self.assertIn('stop-color="#363B47"', green_bike.split('id="rd-dark"')[1][:200])
        self.assertIn('stop-color="#D2F77A"', helmet_face.phone_face_svg())
        self.assertIn('stop-color="#3C414E"', helmet_face.phone_face_svg("midnight"))


class PaintInAppTests(unittest.TestCase):
    TOKEN = "paint-test-token"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (mock.patch.dict(os.environ, {"APPDATA": self.temp.name}),
                        mock.patch.object(app, "PHONE_SESSION_TOKEN", self.TOKEN), mock.patch.dict(app.PHONE_CONTACT)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(paint_jobs.set_current, paint_jobs.DEFAULT)
        self.bridge = mock.Mock()

    def test_a_command_paints_it(self):
        self.assertEqual(app.paint_reply("paint it green", self.bridge), "Fresh paint: Kawasaki green!")
        self.bridge.paint_sig.emit.assert_called_once_with("lime")
        self.assertEqual(app.load_settings()["paint"], "lime")
        self.assertEqual(app.paint_reply("change the paint to kawasaki green", self.bridge), "I'm already in Kawasaki green.")
        for other in ("open paint", "paint a picture of a red car", "paint"):
            self.assertIsNone(app.paint_reply(other, self.bridge), other)
        with mock.patch.object(app, "_execute_smart_action_locked") as task:
            self.assertEqual(app.execute_smart_action("repaint the bike gold", self.bridge, 1000, 800), "Fresh paint: Gold flake!")
        task.assert_not_called()

    def test_the_phone_picks_a_paint_and_gets_its_page_in_it(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app.make_phone_handler(self.bridge, 1000, 800))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f"http://127.0.0.1:{server.server_address[1]}"
        answer = json.loads(opener.open(f"{base}/paint?set=red&token={self.TOKEN}", timeout=5).read())
        self.assertEqual(answer, {"paint": "red", "say": "Fresh paint: Racing red!"})
        self.assertEqual(app.phone_status()["paint"], "red")
        page = opener.open(f"{base}/?token={self.TOKEN}", timeout=5).read().decode("utf-8")
        self.assertIn('const PAGE_PAINT = "red"', page)
        self.assertIn('stop-color="#FF9A90"', page)
        self.assertIn('"label": "Kawasaki green"', page)
        self.assertEqual(json.loads(opener.open(f"{base}/paint?set=purple&token={self.TOKEN}", timeout=5).read())["paint"], "red")


if __name__ == "__main__":
    unittest.main()
