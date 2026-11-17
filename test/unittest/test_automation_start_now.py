"""
C1: "Start now" ends ARM's manual wait and keeps the current title (not marked confirmed).
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
from arm.automation import confidence, notify, store  # noqa: E402
from arm.models.job import JobState  # noqa: E402
from arm.ripper import utils  # noqa: E402
from arm.ui import app  # noqa: E402
from arm.ui.automation import automation as routes  # noqa: E402


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp, "arm.db"),
                                                 "START_NOW_BUTTONS": True, "NTFY_SERVER": "", "NTFY_TOPIC": ""})
        self.patch.start()
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

    def tearDown(self):
        self.patch.stop()
        app.config["WTF_CSRF_ENABLED"] = True
        shutil.rmtree(self.tmp, ignore_errors=True)


def a_job(status="waiting"):
    return SimpleNamespace(job_id=7, title="It", year="2017", status=status, title_manual=None, label="IT",
                           finished=status in ("success", "fail"))


class TestStartNow(Temp):

    def post(self, job, action="start_now"):
        query = MagicMock()
        query.get.return_value = job
        with patch.object(routes.Job, "query", query), patch.dict(app.config, {"LOGIN_DISABLED": True}):
            return self.client.post("/automation/job_action", data={"job": 7, "action": action})

    def test_button_sets_flag_and_keeps_title(self):
        """CHECK Start now records the request and doesn't touch the title"""
        job = a_job()
        response = self.post(job)
        self.assertTrue(response.get_json()["success"])
        self.assertTrue(store.load_job(7)["start_now"])
        self.assertIsNone(job.title_manual)

    def test_not_waiting(self):
        """CHECK a job that isn't waiting can't be started"""
        self.assertEqual(self.post(a_job("ripping")).status_code, 400)

    def test_switched_off(self):
        """CHECK the setting turns the button off"""
        cfg.arm_config["START_NOW_BUTTONS"] = False
        self.assertEqual(self.post(a_job()).status_code, 400)

    def test_phone_button(self):
        """CHECK the signed phone link starts the job"""
        query = MagicMock()
        query.get.return_value = a_job()
        with patch.object(routes.Job, "query", query):
            form = {"job": 7, "action": "start", "token": notify.sign(7, "start"), "from_page": "1"}
            response = self.client.post("/automation/act", data=form)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"starts ripping", response.data)
        self.assertTrue(store.load_job(7)["start_now"])

    def test_wait_loop_ends_on_flag(self):
        """CHECK the ripper's wait ends at the next check, without applying manual values"""
        job = SimpleNamespace(job_id=7, title_manual=None,
                              config=SimpleNamespace(MANUAL_WAIT=True, MANUAL_WAIT_TIME=600))
        store.update_job(7, start_now=True)
        session = MagicMock()
        with patch.object(utils.db, "session", session), patch.object(utils.time, "sleep"), \
                patch.object(utils, "database_updater") as update, \
                patch.object(utils, "apply_manual_overrides") as overrides:
            utils.check_for_wait(job)
        self.assertEqual(session.refresh.call_count, 1)
        overrides.assert_not_called()
        self.assertEqual(update.call_args[0][0], {"status": JobState.IDLE.value})
        self.assertFalse(store.load_job(7)["start_now"])

    def test_notification_has_start_button_when_room(self):
        """CHECK the low-confidence notification offers Start now (ntfy allows 3 buttons)"""
        job = SimpleNamespace(job_id=7, title="It", year="2017", hasnicetitle=True, devpath="/dev/sr0",
                              label="IT", poster_url=None)
        with patch.object(notify, "send") as send, patch.object(notify, "in_app"):
            confidence.ask_user(job, {"score": 40, "reasons": ["x"]}, 900)
        labels = [action["label"] for action in send.call_args[1]["actions"]]
        self.assertIn("Start now", labels)
        self.assertLessEqual(len(labels), 3)


if __name__ == '__main__':
    unittest.main()
