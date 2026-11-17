"""
Configurable naming patterns (C29): token filling and tidying, today's names as the defaults,
destination() with custom patterns, a retitle re-applying the pattern, and the Settings preview.
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import naming, organize, store  # noqa: E402
from arm.ui import app  # noqa: E402


def movie(**fields):
    base = {"job_id": 1, "title": "Fast Five", "title_manual": None, "year": "2011", "imdb_id": "tt1596343",
            "video_type": "movie"}
    base.update(fields)
    return SimpleNamespace(**base)


def patterns(**overrides):
    values = {"MOVIE_NAME_PATTERN": "{Title} ({Year})", "EPISODE_NAME_PATTERN": "{Show} S{Season:00}E{Episode:00}",
              "SEASON_FOLDER_PATTERN": "Season {Season:00}", "EXTRAS_SUB": "extras", "ORGANIZE_EXTRAS": True}
    values.update(overrides)
    return patch.dict(cfg.arm_config, values)


class TestRender(unittest.TestCase):

    def test_tokens_padding_and_tidying(self):
        self.assertEqual(naming.render("{Show} S{Season:00}E{Episode:00}", {"Show": "X", "Season": 3, "Episode": 7}),
                         "X S03E07")
        self.assertEqual(naming.render("{Title} ({Year})", {"Title": "Up", "Year": ""}), "Up")
        empty = {"Title": "Up", "Edition": "", "ImdbId": ""}
        self.assertEqual(naming.render("{Title} - {Edition} - [{ImdbId}]", empty), "Up")
        self.assertEqual(naming.render("{Title} {Nope}", {"Title": "Up"}), "Up")

    def test_defaults_are_todays_names(self):
        with patterns():
            self.assertEqual(naming.movie_name(movie()), "Fast Five (2011)")
            self.assertEqual(naming.movie_name(movie(year="0000")), "Fast Five")
            self.assertEqual(naming.movie_name(movie(), "Extended cut (+12 min)"),
                             "Fast Five (2011) - Extended cut (+12 min)")
            self.assertEqual(naming.season_folder(2), "Season 02")
            self.assertEqual(naming.episode_name(movie(title="Doctor Who", video_type="series"), 11, 1),
                             "Doctor Who S11E01")

    def test_custom_patterns_in_destination(self):
        job = movie(title="Doctor Who", year="2005", video_type="series")
        with patterns(EPISODE_NAME_PATTERN="{Show} - {Season}x{Episode:00} - {EpisodeTitle}",
                      SEASON_FOLDER_PATTERN="S{Season}"), \
                patch.object(naming, "episode_title", return_value="Rosa"):
            target = organize.destination(job, {"role": "episode", "tid": 2, "label": ""}, "/tv/Doctor Who (2005)",
                                          (11, 3))
        self.assertEqual(target, os.path.join("/tv/Doctor Who (2005)", "S11", "Doctor Who - 11x03 - Rosa.mkv"))
        with patterns(MOVIE_NAME_PATTERN="{Title}.{Year} {Edition}"):
            main = organize.destination(movie(), {"role": "main", "tid": 0, "label": ""}, "/m")
            cut = organize.destination(movie(), {"role": "cut", "tid": 1, "label": "Extended cut"}, "/m")
        self.assertEqual((main, cut), (os.path.join("/m", "Fast Five.2011.mkv"),
                                       os.path.join("/m", "Fast Five.2011 Extended cut.mkv")))

    def test_unsafe_or_empty_pattern_falls_back(self):
        with patterns(MOVIE_NAME_PATTERN="{Nothing}"):
            self.assertEqual(naming.movie_name(movie()), "Fast Five (2011)")
        with patterns(MOVIE_NAME_PATTERN="{Title}/{Year}"):
            self.assertNotIn("/", naming.movie_name(movie()))

    def test_preview(self):
        self.assertEqual(naming.preview("season", "Season {Season:00}"), "Season 11/")
        self.assertEqual(naming.preview("episode", "{Show} S{Season:00}E{Episode:00} - {EpisodeTitle}"),
                         "Doctor Who S11E01 - The Woman Who Fell to Earth.mkv")
        self.assertIn("Fast Five (2011) - Extended cut", naming.preview("movie", "{Title} ({Year})"))
        with patch.dict(app.config, {"LOGIN_DISABLED": True}):
            client = app.test_client()
            response = client.get("/automation/name_preview?key=MOVIE_NAME_PATTERN&pattern={Title}")
            self.assertEqual(response.get_json()["name"].split(" · ")[0], "Fast Five.mkv")
            self.assertEqual(client.get("/automation/name_preview?key=NOPE&pattern=x").status_code, 400)


class TestRetitle(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.completed = os.path.join(self.tmp.name, "completed")
        self.old = os.path.join(self.completed, "movies", "Fast-Five (2011)")
        os.makedirs(self.old)
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp.name, "arm.db"),
                                                 "COMPLETED_PATH": self.completed, "WRITE_NFO": False,
                                                 "MOVIE_NAME_PATTERN": "{Title} [{ImdbId}]"})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_retitle_applies_the_pattern_and_keeps_records_right(self):
        main = os.path.join(self.old, "Fast-Five (2011).mkv")
        open(main, "w").close()
        store.update_job(1, files=[{"tid": 0, "role": "main", "file": main}],
                         titles={"titles": [{"tid": 0, "role": "main", "label": ""}]})
        job = movie()
        with patch("arm.ripper.utils.convert_job_type", return_value="movies"):
            new_dir = organize.retitle(job, self.old, "Fast-Five (2011)")
        expected = os.path.join(new_dir, "Fast Five [tt1596343].mkv")
        self.assertTrue(os.path.isfile(expected))
        self.assertEqual(store.load_job(1)["files"][0]["file"], expected)


if __name__ == '__main__':
    unittest.main()
