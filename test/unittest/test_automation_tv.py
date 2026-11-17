"""
Movie or TV show: TV box-set labels, the disc database match the label search mustn't override,
TV-aware confidence and suggestions, the movie/TV choice in the title search, and switching a
job between movie and TV show.
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
from arm.automation import confidence, notify, store  # noqa: E402
from arm.ripper import identify, utils  # noqa: E402
from arm.ui import app, metadata  # noqa: E402
from arm.ui.automation import automation as routes  # noqa: E402
from arm.ui.jobs import jobs as jobs_routes  # noqa: E402

LABEL = "DR_WHO_COMPLETE_SERIES_2018_D23"
DOCTOR_WHO = {"tmdb_id": 57243, "title": "Doctor Who", "year": "2005", "imdb_id": "tt0436992",
              "poster_url": "https://image.tmdb.org/dw.jpg", "popularity": 90, "video_type": "series"}


def update(args, target):
    for key, value in args.items():
        setattr(target, key, value)


def disc(**fields):
    values = dict(job_id=41, label=LABEL, title="King-Kong-Escapes", year="1967", hasnicetitle=True,
                  imdb_id="tt0061868", crc_id=None, disctype="dvd", devpath="/dev/sr0", title_manual=None,
                  poster_url=None, video_type="movie", config=SimpleNamespace(MANUAL_WAIT_TIME=300))
    values.update(fields)
    return SimpleNamespace(**values)


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp, "arm.db"), "NTFY_SERVER": "",
                                                 "NTFY_TOPIC": "", "TMDB_API_KEY": "key"})
        self.patch.start()
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

    def tearDown(self):
        self.patch.stop()
        app.config["WTF_CSRF_ENABLED"] = True
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestTvLabels(unittest.TestCase):

    def test_box_set_words_are_not_part_of_the_title(self):
        self.assertEqual(confidence.clean_label(LABEL), "dr who")
        self.assertEqual(confidence.clean_label("FRIENDS_SEASON_3_DISC_2"), "friends")
        self.assertEqual(confidence.clean_label("THE_OFFICE_S02_D1"), "the office")
        self.assertEqual(confidence.clean_label("DONT_BREATHE_WS_D1"), "dont breathe")

    def test_dr_means_doctor(self):
        self.assertEqual(confidence.similarity(LABEL, "Doctor Who"), 1.0)
        self.assertLess(confidence.similarity(LABEL, "King Kong Escapes"), 0.3)

    def test_looks_like_tv(self):
        for label in (LABEL, "FRIENDS_SEASON_3_DISC_2", "THE_OFFICE_S02_D1", "LOST_S1_D3"):
            self.assertTrue(confidence.looks_like_tv(label), label)
        for label in ("WORLD_WAR_Z", "DONT_BREATHE_WS_D1", "IT_CHAPTER_TWO", "SEVEN_SAMURAI"):
            self.assertFalse(confidence.looks_like_tv(label), label)


class TestDiscDatabaseMatch(unittest.TestCase):
    """Real case, job 41: the disc database said Doctor Who; the label search found King Kong Escapes"""

    def test_database_match_is_kept_when_the_search_fits_the_label_worse(self):
        job = disc()
        database = {"title": "Doctor Who", "year": "2005-", "imdb_id": "tt0436992", "video_type": "series",
                    "poster_url": None, "hasnicetitle": True}
        with patch.object(utils, "database_updater", side_effect=update):
            identify._keep_disc_db_match(job, database)
        self.assertEqual((job.title, job.video_type, job.imdb_id), ("Doctor Who", "series", "tt0436992"))

    def test_better_search_result_wins(self):
        job = disc(label="SPIRITED_AWAY", title="Spirited-Away", imdb_id="tt0245429", video_type="movie")
        database = {"title": "Totoro", "year": "1988", "imdb_id": "tt0096283", "video_type": "movie",
                    "poster_url": None, "hasnicetitle": True}
        with patch.object(utils, "database_updater", side_effect=update) as updater:
            identify._keep_disc_db_match(job, database)
        updater.assert_not_called()
        self.assertEqual(job.title, "Spirited-Away")


class TestTvConfidence(Temp):

    def test_episode_length_is_compared_for_tv_shows(self):
        job = disc(title="Doctor Who", imdb_id="tt0436992", video_type="series", year="2005")
        lengths = [13140, 2700, 2760, 2640, 2700]  # a 219-minute "play all" and 45-minute episodes
        with patch.object(confidence.tmdb, "runtime_minutes", return_value=45), \
                patch.object(confidence, "prior_rip", return_value=None):
            result = confidence.evaluate(job, max(lengths), lengths)
        self.assertGreaterEqual(result["score"], 90)
        self.assertIn("An episode-length title's length matches TMDb's episode runtime", result["reasons"])

    def test_tv_label_switches_a_wrong_movie_to_the_tv_show(self):
        job = disc()
        runtimes = {"tt0061868": 104, "tt0436992": 45}
        with patch.object(confidence, "title_lengths", return_value=[13140, 2700, 2760]), \
                patch.object(confidence.tmdb, "runtime_minutes", side_effect=runtimes.get), \
                patch.object(confidence.tmdb, "search_tv", return_value=[DOCTOR_WHO]), \
                patch.object(confidence.tmdb, "search_movies", return_value=[]), \
                patch.object(confidence, "prior_rip", return_value=None), \
                patch.object(utils, "database_updater", side_effect=update):
            result = confidence.assess(job)
        self.assertEqual((job.title, job.video_type, job.imdb_id), ("Doctor Who", "series", "tt0436992"))
        self.assertEqual(result["level"], "high")
        self.assertIn("The label looks like a TV show", result["reasons"][0])

    def test_low_score_gets_a_suggestion(self):
        job = disc(label="DR_WHO", title="King-Kong-Escapes")  # not a box-set label: nothing adopted
        with patch.object(confidence, "title_lengths", return_value=[13140]), \
                patch.object(confidence.tmdb, "runtime_minutes", return_value=104), \
                patch.object(confidence.tmdb, "search_tv", return_value=[DOCTOR_WHO]), \
                patch.object(confidence.tmdb, "search_movies", return_value=[]), \
                patch.object(confidence, "prior_rip", return_value=None), \
                patch.object(utils, "database_updater", side_effect=update):
            result = confidence.assess(job)
        self.assertEqual(result["level"], "low")
        self.assertEqual(job.title, "King-Kong-Escapes")
        self.assertEqual(result["suggestion"]["title"], "Doctor Who")
        self.assertEqual(result["suggestion"]["video_type"], "series")
        self.assertEqual(confidence.suggestion_name(result["suggestion"]), "Doctor Who (TV show, 2005)")

    def test_notification_offers_the_suggestion(self):
        job = disc()
        result = {"score": 10, "reasons": ["x"], "level": "low", "suggestion": dict(DOCTOR_WHO)}
        with patch.object(notify, "send") as send, patch.object(notify, "in_app"):
            confidence.ask_user(job, result, 900)
        actions = send.call_args.kwargs["actions"]
        self.assertEqual([a["label"] for a in actions], ["Yes, that's it", "Use Doctor Who (TV show, 2005)",
                                                         "Pick the title"])
        self.assertIn("Maybe it's Doctor Who (TV show, 2005)?", send.call_args.args[1])


class TestTitleSearchTypes(unittest.TestCase):

    def response(self, payload):
        return MagicMock(text=json.dumps(payload))

    def test_tv_result_gets_the_tv_shows_imdb_id(self):
        """TV and movie ids overlap: a TV result must not be looked up as a movie"""
        with patch.dict(cfg.arm_config, {"TMDB_API_KEY": "k"}), \
                patch.object(metadata.requests, "get", return_value=self.response({"imdb_id": "tt0436992"})) as get:
            self.assertEqual(metadata.tmdb_get_imdb(57243, "series"), "tt0436992")
        self.assertIn("/tv/57243/external_ids", get.call_args.args[0])

    def test_both_returns_movies_and_tv_shows_labelled(self):
        movie = {"total_results": 1, "results": [{"id": 1, "title": "Dr. Who and the Daleks", "poster_path": None,
                                                  "release_date": "1965-06-23", "backdrop_path": None,
                                                  "overview": ""}]}
        tv = {"total_results": 1, "results": [{"id": 57243, "name": "Doctor Who", "poster_path": None,
                                               "first_air_date": "2005-03-26", "backdrop_path": None,
                                               "overview": ""}]}

        def fake_get(url, params=None):
            return self.response(tv if url.endswith("/tv") else movie)

        with patch.dict(cfg.arm_config, {"TMDB_API_KEY": "k"}), \
                patch.object(metadata.requests, "get", side_effect=fake_get), \
                patch.object(metadata, "tmdb_get_imdb", return_value="tt1"):
            both = metadata.tmdb_search("dr who", "", "both")
            shows = metadata.tmdb_search("dr who", "", "series")
        self.assertEqual([(r["Title"], r["Type"]) for r in both["Search"]],
                         [("Dr. Who and the Daleks", "movie"), ("Doctor Who", "series")])
        self.assertEqual([r["Title"] for r in shows["Search"]], ["Doctor Who"])


class TestTypeRoutes(Temp):

    def waiting_job(self, **fields):
        values = dict(job_id=41, title="King-Kong-Escapes", year="1967", video_type="movie", imdb_id="tt0061868",
                      poster_url=None, label=LABEL, status="waiting", title_manual=None, year_manual=None,
                      video_type_manual=None, imdb_id_manual=None, poster_url_manual=None, hasnicetitle=True,
                      finished=False, path=None)
        values.update(fields)
        return SimpleNamespace(**values)

    def post(self, job, **form):
        query = MagicMock()
        query.get.return_value = job
        with patch.object(routes.Job, "query", query), patch.object(routes.db.session, "commit"), \
                patch.dict(app.config, {"LOGIN_DISABLED": True}):
            return self.client.post("/automation/job_action", data=dict(form, job=job.job_id))

    def test_switch_to_tv_show(self):
        job = self.waiting_job(title="Doctor Who", year="2005")
        reply = self.post(job, action="set_type", type="series")
        self.assertEqual(reply.get_json()["message"], "Doctor Who is now a TV show.")
        self.assertEqual((job.video_type, job.video_type_manual, job.title_manual), ("series", "series", "Doctor Who"))
        self.assertEqual(self.post(job, action="set_type", type="game").status_code, 400)

    def test_use_the_suggestion(self):
        job = self.waiting_job()
        store.update_job(41, confidence={"score": 0, "level": "low", "suggestion": dict(DOCTOR_WHO)},
                         attention=[{"kind": "confidence", "message": "Not sure", "level": "warn"}])
        reply = self.post(job, action="use_suggestion")
        self.assertTrue(reply.get_json()["success"])
        self.assertEqual((job.title_manual, job.year, job.video_type, job.imdb_id),
                         ("Doctor Who", "2005", "series", "tt0436992"))
        self.assertEqual(store.load_job(41)["attention"], [])
        self.assertTrue(store.load_job(41)["confidence"]["confirmed"])

    def test_phone_button_uses_the_suggestion(self):
        job = self.waiting_job()
        store.update_job(41, confidence={"score": 0, "level": "low", "suggestion": dict(DOCTOR_WHO)})
        query = MagicMock()
        query.get.return_value = job
        with patch.object(routes.Job, "query", query), patch.object(routes.db.session, "commit"):
            reply = self.client.post("/automation/act", data={"job": 41, "action": "suggest",
                                                              "token": notify.sign(41, "suggest")})
        self.assertTrue(reply.get_json()["success"])
        self.assertEqual(job.title_manual, "Doctor Who")

    def test_search_results_page_passes_the_type(self):
        job = self.waiting_job()
        query = MagicMock()
        query.get.return_value = job
        found = {"Search": [{"Title": "Doctor Who", "Type": "series", "Year": "2005", "imdbID": "tt0436992",
                             "Poster": ""}]}
        with patch.object(jobs_routes.Job, "query", query), patch.dict(app.config, {"LOGIN_DISABLED": True}), \
                patch.object(jobs_routes.ui_utils, "metadata_selector", return_value=found) as selector:
            html = self.client.get("/list_titles?title=dr+who&year=&job_id=41&type=series").get_data(as_text=True)
        self.assertEqual(selector.call_args.kwargs["media_type"], "series")
        self.assertIn("Doctor Who</strong> - TV show", html)
        self.assertIn('<option value="series" selected>TV shows</option>', html)

    def test_card_shows_the_switch_and_suggestion(self):
        store.update_job(41, confidence={"score": 0, "level": "low", "reasons": ["x"],
                                         "suggestion": dict(DOCTOR_WHO)},
                         attention=[{"kind": "confidence", "message": "Not sure", "level": "warn"}])
        job = self.waiting_job()
        data = jobs_routes._automation_view(job)
        with app.test_request_context("/jobdetail?job_id=41"):
            html = app.jinja_env.get_template("automation_card.html").render(automation=data, jobs=job)
        self.assertIn('data-card-action="use_suggestion">Use Doctor Who (2005), TV show</button>', html)
        self.assertIn('data-type="series"', html)
        self.assertIn("It&#39;s a TV show", html)


if __name__ == '__main__':
    unittest.main()
