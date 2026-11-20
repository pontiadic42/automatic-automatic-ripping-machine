"""
C42: the file editor works while a disc rips; it only refuses changes to the folder a running job uses or
will file into.
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, '/opt/arm')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for LibraryCase
from arm.automation import library  # noqa: E402
from test_automation_library import LibraryCase  # noqa: E402

MOVIE = {"kind": "movie", "year": "2011"}
FAST_FIVE = {"job_id": 47, "name": "Fast Five (2011)", "key": library.title_key("Fast Five", "2011"), "item": None}
UNIDENTIFIED = {"job_id": 48, "name": "DISC_1", "key": "", "item": None}


class TestRunningJobs(LibraryCase):

    def setUp(self):
        super().setUp()
        self.write("movies/Fast-Five (2011)/Fast-Five (2011).mkv")
        self.write("movies/Psych (2006)/Psych (2006).mkv")
        self.write("unidentified/DISC_1 (job 48)/Guess (2001).mkv")
        self.running.stop()
        self.running = patch.object(library, "running_jobs", return_value=[FAST_FIVE, UNIDENTIFIED])
        self.running.start()

    def test_other_titles_can_be_changed_while_a_disc_rips(self):
        library.apply("movies/Psych (2006)", dict(MOVIE, title="Psych", year="2006", kind="series"))
        self.assertTrue(self.exists("tv/Psych (2006)"))

    def test_the_folder_a_running_job_files_into_is_refused(self):
        # same title and year, whatever the spelling
        with self.assertRaisesRegex(library.LibraryError, r"Job 47 \(Fast Five \(2011\)\) is still running"):
            library.apply("movies/Fast-Five (2011)", dict(MOVIE, title="Fast Five"))
        # renaming something else INTO that title is refused too
        with self.assertRaises(library.LibraryError):
            library.apply("movies/Psych (2006)", dict(MOVIE, title="Fast Five"))
        with self.assertRaises(library.LibraryError):
            library.trash("movies/Fast-Five (2011)")
        self.assertTrue(self.exists("movies/Fast-Five (2011)/Fast-Five (2011).mkv"))

    def test_a_running_unidentified_jobs_folder_is_refused(self):
        with self.assertRaisesRegex(library.LibraryError, "Job 48"):
            library.apply("unidentified/DISC_1 (job 48)", dict(MOVIE, title="Real Name", year="2001"))

    def test_undo_back_into_a_running_jobs_folder_is_refused(self):
        with patch.object(library, "running_jobs", return_value=[]):
            entry = library.apply("movies/Fast-Five (2011)", dict(MOVIE, title="Fast Five Renamed"))
        with self.assertRaises(library.LibraryError):
            library.undo(entry["id"])
        self.assertTrue(self.exists("movies/Fast Five Renamed (2011)"))

    def test_when_the_jobs_cant_be_read_nothing_is_changed(self):
        unknown = [{"job_id": "?", "name": "a running job", "key": "", "item": None, "unknown": True}]
        with patch.object(library, "running_jobs", return_value=unknown):
            with self.assertRaises(library.LibraryError):
                library.apply("movies/Psych (2006)", dict(MOVIE, title="Psych", year="2006"))


class TestWhichJobsRun(unittest.TestCase):

    def test_only_live_unfinished_jobs_count(self):
        live = MagicMock(job_id=1, title="Fast Five", title_manual=None, year="2011", label="FF", path=None)
        crashed = MagicMock(job_id=2, title="Old", title_manual=None, year="1999", label="OLD", path=None)
        query = MagicMock()
        query.filter.return_value.all.return_value = [live, crashed]
        with patch("arm.models.job.Job.query", query), \
                patch.object(library, "_alive", side_effect=lambda job: job is live):
            jobs = library.running_jobs()
        self.assertEqual([j["job_id"] for j in jobs], [1])
        self.assertEqual(jobs[0]["key"], library.title_key("Fast-Five", "2011"))


if __name__ == '__main__':
    unittest.main()
