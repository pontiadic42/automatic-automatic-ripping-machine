"""
.nfo files with the TMDb id (C28): movie.nfo / tvshow.nfo content, never overwriting NFOs A2RM
didn't write, rewrite on a type switch, skip unidentified, and the optional tinyMediaManager call.
"""
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import nfo  # noqa: E402


def job(**fields):
    base = {"job_id": 1, "title": "World War Z", "title_manual": None, "year": "2013", "imdb_id": "tt0816711",
            "video_type": "movie"}
    base.update(fields)
    return SimpleNamespace(**base)


class TestNfo(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self.tmp.name, "movies", "World War Z (2013)")
        os.makedirs(self.folder)
        self.patch = patch.dict(cfg.arm_config, {"WRITE_NFO": True, "TMM_URL": "", "TMM_API_KEY": ""})
        self.patch.start()
        self.title = patch("arm.automation.tmdb.title_for_imdb", return_value=None)
        self.title.start()

    def tearDown(self):
        self.title.stop()
        self.patch.stop()
        self.tmp.cleanup()

    def test_movie_nfo(self):
        with patch("arm.automation.tmdb.find_by_imdb", return_value=("movie", 72190)):
            path = nfo.write(job(), self.folder)
        self.assertEqual(os.path.basename(path), "movie.nfo")
        text = open(path, encoding="utf-8").read()
        root = ET.fromstring(text.split("\n", 2)[2])
        self.assertEqual(root.tag, "movie")
        self.assertEqual(root.findtext("title"), "World War Z")
        self.assertEqual(root.findtext("year"), "2013")
        self.assertEqual(root.findtext("tmdbid"), "72190")
        self.assertEqual(root.find("uniqueid[@type='tmdb']").get("default"), "true")
        self.assertEqual(root.findtext("imdbid"), "tt0816711")

    def test_series_nfo_and_type_switch(self):
        with patch("arm.automation.tmdb.find_by_imdb", return_value=("movie", 72190)):
            nfo.write(job(), self.folder)
        with patch("arm.automation.tmdb.find_by_imdb", return_value=("tv", 57243)):
            path = nfo.write(job(title="Doctor Who", year="2005", imdb_id="tt0436992", video_type="series"),
                             self.folder)
        self.assertEqual(os.path.basename(path), "tvshow.nfo")
        self.assertFalse(os.path.exists(os.path.join(self.folder, "movie.nfo")))
        self.assertIn("<imdb_id>tt0436992</imdb_id>", open(path, encoding="utf-8").read())

    def test_special_characters_escaped(self):
        with patch("arm.automation.tmdb.find_by_imdb", return_value=None):
            path = nfo.write(job(title_manual="Fast & Furious <Extended>"), self.folder)
        text = open(path, encoding="utf-8").read()
        self.assertIn("Fast &amp; Furious &lt;Extended&gt;", text)
        self.assertNotIn("tmdbid", text)
        self.assertIn('<uniqueid type="imdb" default="true">', text)

    def test_tmdb_spelling_unless_set_by_hand(self):
        with patch("arm.automation.tmdb.title_for_imdb", return_value="Fast Five"):
            text = nfo.render(job(title="Fast-Five"), "tt1596343", "51497")
            self.assertIn("<title>Fast Five</title>", text)
            text = nfo.render(job(title="Fast-Five", title_manual="My Five"), "tt1596343", "51497")
            self.assertIn("<title>My Five</title>", text)

    def test_never_overwrites_other_nfos(self):
        path = os.path.join(self.folder, "movie.nfo")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("<movie><title>From tinyMediaManager</title></movie>")
        with patch("arm.automation.tmdb.find_by_imdb", return_value=("movie", 72190)):
            self.assertIsNone(nfo.write(job(), self.folder))
        self.assertIn("tinyMediaManager", open(path, encoding="utf-8").read())

    def test_skips(self):
        with patch("arm.automation.tmdb.find_by_imdb", return_value=("movie", 1)):
            self.assertIsNone(nfo.write(job(imdb_id=None), self.folder))
            self.assertIsNone(nfo.write(job(video_type="unknown"), self.folder))
            unidentified = os.path.join(self.tmp.name, "unidentified", "LABEL (job 3)")
            os.makedirs(unidentified)
            self.assertIsNone(nfo.write(job(), unidentified))
            with patch.dict(cfg.arm_config, {"WRITE_NFO": False}):
                self.assertIsNone(nfo.write(job(), self.folder))
        self.assertEqual(os.listdir(self.folder), [])

    def test_tinymediamanager(self):
        self.assertFalse(nfo.tmm_scrape(job()))
        with patch.dict(cfg.arm_config, {"TMM_URL": "http://tmm:7878/", "TMM_API_KEY": "k"}), \
                patch.object(nfo.requests, "post", return_value=SimpleNamespace(status_code=200)) as post:
            self.assertTrue(nfo.tmm_scrape(job(video_type="series")))
        self.assertEqual(post.call_args.args[0], "http://tmm:7878/api/tvshow")
        self.assertEqual(post.call_args.kwargs["headers"], {"api-key": "k"})
        self.assertEqual(post.call_args.kwargs["json"][1], {"action": "scrape", "scope": {"name": "new"}})


if __name__ == '__main__':
    unittest.main()
