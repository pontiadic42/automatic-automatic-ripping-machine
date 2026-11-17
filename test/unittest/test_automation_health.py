"""
Library health check (C27): empty, unfinalized and unreadable files, no video, length far from
TMDb, probable duplicates; when it runs; the daily-summary line; the page.
"""
import datetime
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import health, mkvcheck, store  # noqa: E402
from arm.ui import app  # noqa: E402


class TestHealth(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib = os.path.join(self.tmp.name, "completed")
        os.makedirs(os.path.join(self.lib, "movies", "A (2001)"))
        os.makedirs(os.path.join(self.lib, "movies", "B (2002)"))
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp.name, "arm.db"),
                                                 "COMPLETED_PATH": self.lib, "HEALTH_CHECK_DAYS": 7,
                                                 "HEALTH_CHECK_TIME": "04:00", "HEALTH_CHECK_DECODE": False})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def write(self, rel, data):
        path = os.path.join(self.lib, rel)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_problems_found(self):
        good = self.write("movies/A (2001)/A (2001).mkv", mkvcheck.EBML_MAGIC + b"x" * 100)
        copy = self.write("movies/B (2002)/B (2002).mkv", mkvcheck.EBML_MAGIC + b"x" * 100)
        self.write("movies/A (2001)/empty.mkv", b"")
        self.write("movies/A (2001)/broken.mkv", mkvcheck.UNFINALIZED_TEXT + b" rest")
        audio_only = self.write("movies/B (2002)/audio.mp4", b"y" * 50)
        self.write("movies/B (2002)/notes.txt", b"not a video")

        def probe(path):
            if path == audio_only:
                return {"duration": 60.0, "video": 0}
            return {"duration": 6000.0, "video": 1}
        with patch.object(health, "probe", side_effect=probe), patch.object(health, "DUPLICATE_MIN_BYTES", 1):
            result = health.run(runtimes={good: (130, 12)})
        by_file = {os.path.basename(p["path"]): p["problems"] for p in result["problems"]}
        self.assertEqual(result["files"], 5)
        self.assertIn("empty file", by_file["empty.mkv"][0])
        self.assertIn("unfinalized", by_file["broken.mkv"][0])
        self.assertEqual(by_file["audio.mp4"], ["no video stream"])
        self.assertIn("TMDb says 130 min (job 12)", by_file["A (2001).mkv"][0])
        self.assertTrue(any("duplicate" in p for p in by_file["A (2001).mkv"]))
        self.assertTrue(any("duplicate" in p for p in by_file[os.path.basename(copy)]))
        self.assertEqual(store.load_state()["health"]["files"], 5)

    def test_unreadable_and_decode(self):
        path = self.write("movies/A (2001)/A.mp4", b"z" * 10)
        with patch.object(health, "probe", return_value=None):
            self.assertEqual(health.check_file(path)[0], ["ffprobe can't read it"])
        with patch.object(health, "probe", return_value={"duration": 100.0, "video": 1}), \
                patch.object(health, "decode_errors", return_value="corrupt frame") as decode:
            self.assertEqual(health.check_file(path, decode=True)[0], ["decode errors: corrupt frame"])
            self.assertEqual(health.check_file(path, decode=False)[0], [])
        decode.assert_called_once()

    def test_when_it_runs(self):
        local = datetime.datetime(2026, 11, 17, 5, 0)
        self.assertTrue(health.due(local))
        self.assertFalse(health.due(local.replace(hour=3)))
        store.update_state(last_health="2026-11-12")
        self.assertFalse(health.due(local))
        store.update_state(last_health="2026-11-10")
        self.assertTrue(health.due(local))
        with patch.dict(cfg.arm_config, {"HEALTH_CHECK_DAYS": 0}):
            self.assertFalse(health.due(local))
        with patch.object(health, "busy", return_value=True), patch.object(health, "run") as run:
            self.assertIsNone(health.scheduled(local))
        run.assert_not_called()
        with patch.object(health, "busy", return_value=False), patch.object(health, "run", return_value={}) as run:
            health.scheduled(local)
        run.assert_called_once()
        self.assertEqual(store.load_state()["last_health"], "2026-11-17")

    def test_summary_line_once_per_check(self):
        store.update_state(health={"checked": "2026-11-17T04:00:00", "files": 10,
                                   "problems": [{"path": "/x.mkv", "problems": ["empty file"]}]})
        self.assertIn("1 of 10 files", health.summary_line())
        self.assertIsNone(health.summary_line())

    def test_page(self):
        store.update_state(health={"checked": "2026-11-17T04:00:00", "files": 3, "seconds": 2, "decode": False,
                                   "problems": [{"path": "/lib/x.mkv", "problems": ["no video stream"]}]})
        with patch.dict(app.config, {"LOGIN_DISABLED": True}):
            response = app.test_client().get("/automation/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("/lib/x.mkv", body)
        self.assertIn("no video stream", body)


if __name__ == '__main__':
    unittest.main()
