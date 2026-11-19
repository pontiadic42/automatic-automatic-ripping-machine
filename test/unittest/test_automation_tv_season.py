"""
C33: TV results say which show it is (years, seasons, network), and the user can pick the
season and first episode on the disc; episode naming uses that instead of the label.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import organize, store, tmdb  # noqa: E402
from arm.ui import app, metadata  # noqa: E402
from arm.ui.automation import automation as routes  # noqa: E402
from arm.ui.jobs import jobs as jobs_routes  # noqa: E402

DOCTOR_WHO = {"first_air_date": "2005-03-26", "last_air_date": "2022-10-23", "status": "Ended",
              "number_of_seasons": 13, "networks": [{"name": "BBC One"}], "origin_country": ["GB"],
              "external_ids": {"imdb_id": "tt0436992"},
              "seasons": [{"season_number": 0, "name": "Specials", "air_date": "2005-11-18", "episode_count": 40},
                          {"season_number": 1, "name": "Series 1", "air_date": "2005-03-26", "episode_count": 13},
                          {"season_number": 2, "name": "Series 2", "air_date": "2006-04-15", "episode_count": 13}]}


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp, "arm.db"), "TMDB_API_KEY": "k",
                                                 "TV_SEASON_CHOICE": True, "TV_EPISODE_MATCHING": True})
        self.patch.start()
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

    def tearDown(self):
        self.patch.stop()
        app.config["WTF_CSRF_ENABLED"] = True
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestTvSummary(Temp):

    def test_summary_text(self):
        """CHECK a TV result gets years, seasons, network and country"""
        with patch.object(metadata.requests, "get", return_value=MagicMock(text=json.dumps(DOCTOR_WHO))):
            summary = metadata.tv_summary(57243)
        self.assertEqual(summary["details"], "2005-2022 · 13 seasons · BBC One · GB")
        self.assertEqual(summary["imdb_id"], "tt0436992")

    def test_running_show_and_errors(self):
        """CHECK a running show reads "2023-", and a TMDb error gives nothing"""
        running = dict(DOCTOR_WHO, status="Returning Series", number_of_seasons=1)
        with patch.object(metadata.requests, "get", return_value=MagicMock(text=json.dumps(running))):
            self.assertTrue(metadata.tv_summary(1)["details"].startswith("2005- · 1 season ·"))
        with patch.object(metadata.requests, "get", return_value=MagicMock(text='{"status_code": 34}')):
            self.assertEqual(metadata.tv_summary(1), {})

    def test_season_choices(self):
        """CHECK seasons come from TMDb with specials last"""
        with patch.object(tmdb, "find_by_imdb", return_value=("tv", 57243)), \
                patch.object(tmdb, "tv_details", return_value=DOCTOR_WHO):
            seasons = tmdb.season_choices("tt0436992")
        self.assertEqual([s["number"] for s in seasons], [1, 2, 0])
        self.assertEqual(seasons[1], {"number": 2, "name": "Series 2", "year": "2006", "episodes": 13})


class TestEpisodeNaming(Temp):

    def run_match(self, label, choice=None):
        job = SimpleNamespace(job_id=41, imdb_id="tt0436992", label=label, title="Doctor Who", title_manual=None)
        if choice:
            organize.save_season(41, *choice)
        episodes = [{"tid": tid, "seconds": 45 * 60} for tid in (1, 2, 3)]
        season = {"episodes": [{"episode_number": n, "runtime": 45} for n in range(1, 14)]}
        with patch.object(tmdb, "find_by_imdb", return_value=("tv", 57243)), \
                patch.object(tmdb, "tv_details", return_value=DOCTOR_WHO), \
                patch.object(tmdb, "season", return_value=season) as get_season:
            numbers, message = organize.match_episodes(job, episodes)
        return numbers, message, get_season

    def test_label_without_season_fails_without_choice(self):
        """CHECK a box-set label with no season can't be numbered by itself"""
        numbers, message, _season = self.run_match("DR_WHO_COMPLETE_SERIES_2018_D23")
        self.assertEqual(numbers, {})
        self.assertIn("doesn't say which season", message)

    def test_chosen_season_and_first_episode(self):
        """CHECK the user's season and first episode are used"""
        numbers, message, get_season = self.run_match("DR_WHO_COMPLETE_SERIES_2018_D23", (2, 4))
        self.assertEqual(numbers, {1: (2, 4), 2: (2, 5), 3: (2, 6)})
        self.assertEqual(get_season.call_args[0][1], 2)
        self.assertEqual(message, "Season 2, episodes 4-6")

    def test_choice_ignored_when_switched_off(self):
        """CHECK TV_SEASON_CHOICE off falls back to the label"""
        cfg.arm_config["TV_SEASON_CHOICE"] = False
        numbers, _message, _season = self.run_match("DR_WHO_COMPLETE_SERIES_2018_D23", (2, 4))
        self.assertEqual(numbers, {})


class TestSeasonRoutes(Temp):

    def post(self, job, **form):
        query = MagicMock()
        query.get.return_value = job
        with patch.object(routes.Job, "query", query), patch.dict(app.config, {"LOGIN_DISABLED": True}):
            return self.client.post("/automation/job_action", data=dict(form, job=job.job_id, action="set_season"))

    def test_set_season(self):
        """CHECK the job page saves the season and first episode"""
        job = SimpleNamespace(job_id=41, video_type="series", title="Doctor Who", title_manual=None, label="DR",
                              finished=False)
        reply = self.post(job, season="2", first_episode="4")
        self.assertEqual(reply.get_json()["message"], "Season 2, from episode 4.")
        self.assertEqual(store.load_job(41)["tv_season"], {"season": 2, "first_episode": 4})
        self.assertEqual(self.post(job, season="x").status_code, 400)

    def test_movies_have_no_season(self):
        """CHECK a movie job refuses a season"""
        job = SimpleNamespace(job_id=41, video_type="movie", title="It", title_manual=None, label="IT", finished=False)
        self.assertEqual(self.post(job, season="1").status_code, 400)

    def test_pick_offers_seasons(self):
        """CHECK picking a TV show on the job page (was /gettitle) returns its seasons"""
        details = {"Title": "Doctor Who", "Year": "2005", "imdbID": "tt0436992", "Type": "series",
                   "Poster": "", "Plot": ""}
        seasons = [{"number": 2, "name": "Series 2", "year": "2006", "episodes": 13}]
        with patch.object(jobs_routes.ui_utils, "metadata_selector", return_value=details), \
                patch.object(tmdb, "season_choices", return_value=seasons), \
                patch.dict(app.config, {"LOGIN_DISABLED": True}):
            data = self.client.get("/identify/details?imdbID=tt0436992").get_json()
            old = self.client.get("/gettitle?imdbID=tt0436992&job_id=41")
        self.assertEqual(data["seasons"], seasons)
        self.assertEqual(data["type"], "series")
        self.assertIn("pick=tt0436992", old.location)
        self.assertTrue(old.location.endswith("#identify"))


if __name__ == '__main__':
    unittest.main()
