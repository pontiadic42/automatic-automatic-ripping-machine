"""
C16: low confidence and nobody answered: the disc is filed under unidentified/<label> (job N)
with stills, and accepting or picking the title later moves it to movies/TV.
"""
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import notify, organize, store, unidentified  # noqa: E402
from arm.ui import app  # noqa: E402
from arm.ui.automation import automation as routes  # noqa: E402

LOW = {"level": "low", "score": 30, "reasons": []}


def a_job(**fields):
    values = dict(job_id=40, title="World War Z", year="2013", video_type="movie", imdb_id="tt0816711",
                  label="WORLD_WAR_Z", title_manual=None, disctype="dvd", status="success", finished=True,
                  poster_url=None, year_manual=None, video_type_manual=None, imdb_id_manual=None,
                  poster_url_manual=None, hasnicetitle=True, path=None)
    values.update(fields)
    return SimpleNamespace(**values)


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.completed = os.path.join(self.tmp, "completed")
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp, "arm.db"),
                                                 "COMPLETED_PATH": self.completed,
                                                 "LOW_CONFIDENCE_ACTION": "unidentified",
                                                 "NTFY_SERVER": "", "NTFY_TOPIC": ""})
        self.patch.start()
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

    def tearDown(self):
        self.patch.stop()
        app.config["WTF_CSRF_ENABLED"] = True
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestMarking(Temp):

    def test_unanswered_low_score_goes_to_unidentified(self):
        """CHECK a low score nobody answered files the job under unidentified/<label> (job N)"""
        job = a_job()
        self.assertTrue(unidentified.mark_if_unanswered(job, LOW))
        self.assertEqual(store.load_job(40)["unidentified"]["guess"]["title"], "World War Z")
        self.assertEqual(unidentified.directory(job),
                         os.path.join(self.completed, "unidentified", "World War Z (job 40)"))

    def test_answered_or_high_is_left_alone(self):
        """CHECK a title set or confirmed by the user, Start now, a high score or the old setting keep the guess"""
        self.assertFalse(unidentified.mark_if_unanswered(a_job(title_manual="World War Z"), LOW))
        self.assertFalse(unidentified.mark_if_unanswered(a_job(), {"level": "high"}))
        store.update_job(40, started_now=True)
        self.assertFalse(unidentified.mark_if_unanswered(a_job(), LOW))
        store.update_job(40, started_now=False)
        cfg.arm_config["LOW_CONFIDENCE_ACTION"] = "guess"
        self.assertFalse(unidentified.mark_if_unanswered(a_job(), LOW))

    def test_named_during_rip_goes_to_movies(self):
        """CHECK a title picked after the wait (during the rip) wins over unidentified"""
        unidentified.mark_if_unanswered(a_job(), LOW)
        self.assertIsNone(unidentified.directory(a_job(title_manual="World War Z")))

    def test_still_times(self):
        """CHECK one still near the start, five over the film, the ends skipped"""
        times = unidentified.still_times(7200)
        self.assertEqual(times[0], 120)
        self.assertEqual(len(times), 6)
        self.assertEqual((times[1], times[-1]), (300, 6900))

    def test_empty_folder_removed_but_not_one_with_video(self):
        """CHECK only a folder without video files is removed"""
        empty, full = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        os.makedirs(empty)
        os.makedirs(full)
        open(os.path.join(empty, "poster.jpg"), "w").close()
        open(os.path.join(full, "movie.mkv"), "w").close()
        unidentified.remove_if_empty(empty)
        unidentified.remove_if_empty(full)
        self.assertFalse(os.path.exists(empty))
        self.assertTrue(os.path.exists(full))


class TestAfterRip(Temp):

    def test_stills_and_question_sent(self):
        """CHECK stills are saved and the phone gets "Do you know this one?" with a still attached"""
        job = a_job()
        unidentified.mark_if_unanswered(job, LOW)
        folder = os.path.join(self.completed, "unidentified", "World War Z (job 40)")
        os.makedirs(folder)
        open(os.path.join(folder, "World War Z (2013).mkv"), "w").close()

        def fake_ffmpeg(command, **_kwargs):
            open(command[-1], "w").close()
            return MagicMock()
        with patch.object(unidentified.subprocess, "run", side_effect=fake_ffmpeg), \
                patch.object(organize, "duration_seconds", return_value=7200), \
                patch.object(notify, "send") as send, patch.object(notify, "in_app"):
            unidentified.after_move(job, folder)
        self.assertEqual(len(store.load_job(40)["stills"]), 6)
        self.assertEqual(send.call_args[0][0], "Do you know this one?")
        self.assertIn("/automation/still?job=40&n=0", send.call_args[1]["attach"])
        self.assertEqual(store.load_job(40)["attention"][0]["kind"], "confidence")

    def test_still_route_needs_signature(self):
        """CHECK stills are served only with a valid token"""
        path = os.path.join(self.tmp, "s.jpg")
        with open(path, "wb") as handle:
            handle.write(b"jpeg")
        store.update_job(40, stills=[{"file": path, "at": 120}])
        self.assertEqual(self.client.get("/automation/still?job=40&n=0&token=bad").status_code, 403)
        ok = self.client.get(f"/automation/still?job=40&n=0&token={notify.sign(40, 'still0')}")
        self.assertEqual((ok.status_code, ok.data), (200, b"jpeg"))


class TestAcceptMoves(Temp):

    def test_accept_moves_unidentified_job(self):
        """CHECK Accept title on a finished unidentified job moves its folder to movies"""
        folder = os.path.join(self.completed, "unidentified", "World War Z (job 40)")
        os.makedirs(folder)
        open(os.path.join(folder, "World War Z (2013).mkv"), "w").close()
        job = a_job(path=folder)
        unidentified.mark_if_unanswered(job, LOW)
        query = MagicMock()
        query.get.return_value = job
        with patch.object(routes.Job, "query", query), patch.object(routes.db.session, "commit"), \
                patch("arm.ui.jobs.jobs.db.session.commit"), patch("arm.automation.jellyfin.announce"), \
                patch.dict(app.config, {"LOGIN_DISABLED": True}):
            reply = self.client.post("/automation/job_action", data={"job": 40, "action": "accept"})
        self.assertTrue(reply.get_json()["success"], reply.get_json())
        moved = os.path.join(self.completed, "movies", "World War Z (2013)", "World War Z (2013).mkv")
        self.assertTrue(os.path.exists(moved))
        self.assertEqual(job.title_manual, "World War Z")


if __name__ == '__main__':
    unittest.main()
