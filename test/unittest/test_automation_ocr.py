"""
Title-card OCR (C25): picking title-like phrases from Tesseract's output, matching them against
TMDb by name and runtime, and setting the job's suggestion only for unsure, unnamed jobs.
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import ocr, store  # noqa: E402

FRAMES = ["WARNER BROS. PICTURES PRESENTS\n", "a\n\nWORLD WAR Z\n\n", "World War Z\n|||\n",
          "directed by Marc Forster\n", "1234 5678\n"]


def movie(title, year, runtime, imdb):
    return {"title": title, "year": year, "runtime": runtime, "imdb_id": imdb, "poster_url": None, "popularity": 1}


class TestOcr(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp.name, "arm.db"), "TITLE_OCR": True})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_phrases_skip_credits_and_noise(self):
        self.assertEqual(ocr.phrases(FRAMES), ["World War Z"])

    def test_frame_times_cover_the_first_minutes(self):
        self.assertEqual(ocr.frame_times(7000)[:3], [10, 30, 50])
        self.assertLessEqual(max(ocr.frame_times(7000)), 300)
        self.assertLessEqual(max(ocr.frame_times(200)), 100)

    def test_candidates_need_the_name_and_the_runtime(self):
        results = [movie("World War Z", "2013", 116, "tt0816711"), movie("World War Z", "2025", 90, "tt9"),
                   movie("War of the Worlds", "2005", 116, "tt0407304")]
        with patch("arm.automation.tmdb.search_movies", return_value=results):
            found = ocr.candidates(["World War Z"], 116 * 60 + 30)
        self.assertEqual([c["imdb_id"] for c in found], ["tt0816711"])

    def test_after_move_sets_the_suggestion_for_unsure_jobs(self):
        store.update_job(3, confidence={"level": "low", "score": 30})
        job = SimpleNamespace(job_id=3, title_manual=None, disctype="dvd", video_type="movie")
        results = [movie("World War Z", "2013", 116, "tt0816711")]
        with patch.object(ocr, "available", return_value=True), \
                patch("arm.automation.unidentified.main_file", return_value="/x/main.mkv"), \
                patch("arm.automation.organize.duration_seconds", return_value=116 * 60), \
                patch.object(ocr, "read_text", return_value=FRAMES), \
                patch("arm.automation.tmdb.search_movies", return_value=results):
            found = ocr.after_move(job, "/x")
        self.assertEqual(found[0]["title"], "World War Z")
        data = store.load_job(3)
        self.assertEqual(data["confidence"]["suggestion"]["imdb_id"], "tt0816711")
        self.assertIn("World War Z", ocr.hint(3))
        self.assertTrue(any(a["kind"] == "ocr" for a in data.get("attention", [])))

    def test_confident_or_named_jobs_are_left_alone(self):
        store.update_job(4, confidence={"level": "high", "score": 95})
        job = SimpleNamespace(job_id=4, title_manual=None, disctype="dvd", video_type="movie")
        with patch.object(ocr, "available", return_value=True), patch.object(ocr, "read_text") as read:
            self.assertEqual(ocr.after_move(job, "/x"), [])
            store.update_job(4, confidence={"level": "low"})
            job.title_manual = "Mine"
            self.assertEqual(ocr.after_move(job, "/x"), [])
        read.assert_not_called()

    def test_off_or_no_tesseract(self):
        store.update_job(5, confidence={"level": "low"})
        job = SimpleNamespace(job_id=5, title_manual=None, disctype="dvd", video_type="movie")
        with patch.object(ocr, "read_text") as read:
            with patch.object(ocr, "available", return_value=False):
                self.assertEqual(ocr.after_move(job, "/x"), [])
            with patch.dict(cfg.arm_config, {"TITLE_OCR": False}), patch.object(ocr, "available", return_value=True):
                self.assertEqual(ocr.after_move(job, "/x"), [])
        read.assert_not_called()


if __name__ == '__main__':
    unittest.main()
