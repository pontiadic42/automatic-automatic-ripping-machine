"""
C30: titles keep their spaces ("World War Z", not "World-War-Z"), and an identified
title takes TMDb's spelling when it has the same words.
"""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
from arm.automation import confidence, tmdb  # noqa: E402
from arm.ripper import identify, utils  # noqa: E402


class TestCleanForFilename(unittest.TestCase):

    def test_keeps_spaces(self):
        """CHECK words stay separated by spaces"""
        self.assertEqual(utils.clean_for_filename("World War Z"), "World War Z")
        self.assertEqual(utils.clean_for_filename("Pirates of the Caribbean: At World's End"),
                         "Pirates of the Caribbean- At Worlds End")
        self.assertEqual(utils.clean_for_filename("Fast & Furious  [2009]"), "Fast and Furious")

    def test_hyphen_form_for_music(self):
        """CHECK spaces=False keeps the old hyphenated form"""
        self.assertEqual(utils.clean_for_filename("Pink Floyd", spaces=False), "Pink-Floyd")

    def test_tmdb_result_title_has_spaces(self):
        """CHECK update_job stores TMDb's title with spaces"""
        job = SimpleNamespace(job_id=1)
        results = {"Search": [{"Year": "2013", "Title": "World War Z", "Type": "movie",
                               "imdbID": "tt0816711", "Poster": None}]}
        with patch.object(identify.utils, "database_updater") as update:
            identify.update_job(job, results)
        self.assertEqual(update.call_args[0][0]["title"], "World War Z")


class TestPreferTmdbTitle(unittest.TestCase):

    def run_prefer(self, title, tmdb_title, title_manual=None):
        job = SimpleNamespace(job_id=40, title=title, title_manual=title_manual, imdb_id="tt0816711")
        with patch.object(tmdb, "title_for_imdb", return_value=tmdb_title), \
                patch.object(utils, "database_updater") as update:
            result = confidence.prefer_tmdb_title(job)
        return result, update

    def test_hyphenated_title_respelled(self):
        """CHECK World-War-Z from the disc database becomes World War Z"""
        result, update = self.run_prefer("World-War-Z", "World War Z")
        self.assertEqual(result, "World War Z")
        self.assertEqual(update.call_args[0][0], {"title": "World War Z", "title_auto": "World War Z"})

    def test_different_title_left_alone(self):
        """CHECK a TMDb title with other words never replaces the identification"""
        result, update = self.run_prefer("World-War-Z", "World War Z 2")
        self.assertIsNone(result)
        update.assert_not_called()

    def test_manual_title_left_alone(self):
        """CHECK a title set by the user is never changed"""
        result, update = self.run_prefer("World-War-Z", "World War Z", title_manual="World-War-Z")
        self.assertIsNone(result)
        update.assert_not_called()

    def test_no_tmdb_answer(self):
        """CHECK no TMDb key / no answer changes nothing"""
        result, update = self.run_prefer("World-War-Z", None)
        self.assertIsNone(result)
        update.assert_not_called()

    def test_title_for_imdb(self):
        """CHECK title_for_imdb reads a movie title and a TV show name"""
        with patch.object(tmdb, "find_by_imdb", return_value=("movie", 1)), \
                patch.object(tmdb, "movie_details", return_value={"title": "World War Z"}):
            self.assertEqual(tmdb.title_for_imdb("tt0816711"), "World War Z")
        with patch.object(tmdb, "find_by_imdb", return_value=("tv", 2)), \
                patch.object(tmdb, "tv_details", return_value={"name": "Doctor Who"}):
            self.assertEqual(tmdb.title_for_imdb("tt0436992"), "Doctor Who")


if __name__ == '__main__':
    unittest.main()
