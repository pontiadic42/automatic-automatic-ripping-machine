"""
C9: a database lock when the manual wait ends must not kill the job or leave it on
"waiting". Failed commits are rolled back and retried; the end of a job is saved even
when the session was left broken.
"""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/opt/arm')
from sqlalchemy.exc import OperationalError, PendingRollbackError  # noqa: E402

from arm.models.job import JobState  # noqa: E402
from arm.ripper import main as ripper_main  # noqa: E402
from arm.ripper import utils  # noqa: E402
from arm.ui import db_watch  # noqa: E402


def locked():
    return OperationalError("UPDATE job", {}, Exception("database is locked"))


class TestDatabaseUpdater(unittest.TestCase):

    def run_updater(self, commit_effects, wait_time=5):
        session = MagicMock()
        session.commit.side_effect = commit_effects
        job = SimpleNamespace(job_id=1, status="waiting")

        def rollback():
            job.status = "expired"   # a rollback expires what wasn't committed
        session.rollback.side_effect = rollback
        with patch.object(utils.db, "session", session), patch.object(utils.time, "sleep"):
            result = utils.database_updater({"status": "active"}, job, wait_time=wait_time)
        return result, job, session

    def test_rolls_back_and_retries_after_lock(self):
        """CHECK a locked commit is rolled back, the values set again and the retry succeeds"""
        result, job, session = self.run_updater([locked(), None])
        self.assertTrue(result)
        self.assertEqual(session.rollback.call_count, 1)
        self.assertEqual(job.status, "active")

    def test_pending_rollback_counts_as_locked(self):
        """CHECK PendingRollbackError wrapping a lock is retried, not raised"""
        error = PendingRollbackError("Session's transaction has been rolled back")
        error.orig = Exception("database is locked")
        result, job, _session = self.run_updater([error, None])
        self.assertTrue(result)
        self.assertEqual(job.status, "active")

    def test_gives_up_with_values_kept_and_session_usable(self):
        """CHECK after the last try the session is rolled back and the values stay on the job"""
        result, job, session = self.run_updater([locked()] * 3, wait_time=3)
        self.assertFalse(result)
        self.assertEqual(session.rollback.call_count, 3)
        self.assertEqual(job.status, "active")

    def test_other_errors_roll_back_and_raise(self):
        """CHECK a non-lock error leaves the session usable and raises"""
        with self.assertRaises(RuntimeError):
            self.run_updater([OperationalError("x", {}, Exception("disk I/O error"))])

    def test_adder_rolls_back_before_retry(self):
        """CHECK database_adder rolls back a locked add and adds again"""
        session = MagicMock()
        session.commit.side_effect = [locked(), None]
        with patch.object(utils.db, "session", session), patch.object(utils.time, "sleep"):
            self.assertTrue(utils.database_adder(object()))
        self.assertEqual(session.rollback.call_count, 1)
        self.assertEqual(session.add.call_count, 2)


class TestRecordJobEnd(unittest.TestCase):

    def make_job(self, status, eject_error=None):
        job = SimpleNamespace(job_id=29, status=status, start_time=None, drive=MagicMock())
        job.eject = MagicMock(side_effect=eject_error)
        return job

    def test_job_left_waiting_is_marked_failed(self):
        """CHECK a job that ends while still 'waiting' is saved as failed with a reason"""
        job = self.make_job(JobState.MANUAL_WAIT_STARTED.value)
        with patch.object(ripper_main.utils, "database_updater", return_value=True) as update:
            ripper_main.record_job_end(job)
        values = update.call_args[0][0]
        self.assertEqual(values["status"], JobState.FAILURE.value)
        self.assertIn("ARM stopped", values["errors"])
        self.assertEqual(values["job_length"], "0:00:00")

    def test_eject_failure_still_saves_status(self):
        """CHECK a failing eject (locked DB) still releases the drive and saves the status"""
        job = self.make_job(JobState.FAILURE.value, eject_error=PendingRollbackError("rolled back"))
        job.errors = "database is locked"
        with patch.object(ripper_main.utils, "database_updater", return_value=True) as update, \
                patch.object(ripper_main.utils, "_safe_rollback"):
            ripper_main.record_job_end(job)
        values = update.call_args[0][0]
        self.assertEqual(values["status"], JobState.FAILURE.value)
        self.assertEqual(values["errors"], "database is locked")
        self.assertIsNone(job.drive.job_id_current)

    def test_success_kept(self):
        """CHECK a successful job stays successful"""
        job = self.make_job(JobState.SUCCESS.value)
        with patch.object(ripper_main.utils, "database_updater", return_value=True) as update:
            ripper_main.record_job_end(job)
        self.assertEqual(update.call_args[0][0]["status"], JobState.SUCCESS.value)
        self.assertNotIn("errors", update.call_args[0][0])


class TestWaitSurvivesLock(unittest.TestCase):

    def test_refresh_error_does_not_end_wait(self):
        """CHECK a locked re-read during the manual wait is skipped, not fatal"""
        job = SimpleNamespace(job_id=1, title_manual=None,
                              config=SimpleNamespace(MANUAL_WAIT=True, MANUAL_WAIT_TIME=10))
        session = MagicMock()
        session.refresh.side_effect = [locked(), None]
        with patch.object(utils.db, "session", session), patch.object(utils.time, "sleep"), \
                patch.object(utils, "database_updater", return_value=True) as update:
            utils.check_for_wait(job)
        self.assertEqual(session.refresh.call_count, 2)
        self.assertEqual(update.call_args[0][0], {"status": JobState.IDLE.value})


class TestSlowTransactionLog(unittest.TestCase):

    def test_long_transaction_logged(self):
        """CHECK a transaction held longer than SLOW_SECONDS is logged with who held it"""
        conn = SimpleNamespace(info={})
        with patch.object(db_watch.time, "monotonic", side_effect=[100.0, 100.0 + db_watch.SLOW_SECONDS + 5]):
            db_watch._begin(conn)
            with self.assertLogs(level="WARNING") as logs:
                db_watch._end(conn)
        self.assertIn("held for 25 s", logs.output[0])


if __name__ == '__main__':
    unittest.main()
