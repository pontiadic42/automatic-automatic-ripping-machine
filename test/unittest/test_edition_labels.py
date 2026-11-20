"""
C40: label movie versions (Theatrical, Extended cut, ...) in the file editor, from the Fast Five folder
(job 42: "Fast-Five (2011).mkv" and "Fast-Five (2011) - Version 2.mkv").
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for LibraryCase
from arm.automation import library  # noqa: E402
from test_automation_library import LibraryCase  # noqa: E402
from arm.ui import app  # noqa: E402

BASE = "movies/Fast-Five (2011)"
MOVIE = {"kind": "movie", "title": "Fast Five", "year": "2011"}


class TestEditionLabels(LibraryCase):

    def setUp(self):
        super().setUp()
        self.write(f"{BASE}/Fast-Five (2011).mkv", 100)
        self.write(f"{BASE}/Fast-Five (2011) - Version 2.mkv", 99)
        self.write(f"{BASE}/Fast-Five (2011).en.srt")
        self.write(f"{BASE}/extras/Fast Five (2).mkv")
        self.roles = {"Fast-Five (2011).mkv": "main", "Fast-Five (2011) - Version 2.mkv": "version",
                      "extras/Fast Five (2).mkv": "extra"}

    def moves(self, editions):
        plan = library.plan(BASE, MOVIE, self.roles, editions)
        return {m["from"].split("/", 2)[2]: m["to"] for m in plan["moves"]}

    def test_both_cuts_get_their_labels_and_the_folder_loses_its_hyphen(self):
        moves = self.moves({"Fast-Five (2011).mkv": "Theatrical", "Fast-Five (2011) - Version 2.mkv": "Extended cut"})
        folder = "movies/Fast Five (2011)"
        self.assertEqual(moves["Fast-Five (2011).mkv"], f"{folder}/Fast Five (2011) - Theatrical.mkv")
        self.assertEqual(moves["Fast-Five (2011) - Version 2.mkv"], f"{folder}/Fast Five (2011) - Extended cut.mkv")
        # the subtitle follows its video, extras stay extras
        self.assertEqual(moves["Fast-Five (2011).en.srt"], f"{folder}/Fast Five (2011) - Theatrical.en.srt")
        self.assertEqual(moves["extras/Fast Five (2).mkv"], f"{folder}/extras/Fast Five (2).mkv")

    def test_no_label_keeps_todays_names(self):
        moves = self.moves({})
        self.assertEqual(moves["Fast-Five (2011).mkv"], "movies/Fast Five (2011)/Fast Five (2011).mkv")
        self.assertEqual(moves["Fast-Five (2011) - Version 2.mkv"],
                         "movies/Fast Five (2011)/Fast Five (2011) - Version 2.mkv")

    def test_typed_labels_are_made_safe(self):
        self.assertEqual(library.clean_editions({"a": "  Director's  cut / 4K? ", "b": "   ", "c": None}),
                         {"a": "Director's cut 4K"})
        self.assertEqual(len(library.clean_editions({"a": "x" * 99})["a"]), library.EDITION_MAX)

    def test_tv_ignores_labels(self):
        plan = library.plan(BASE, dict(MOVIE, kind="series"), None, {"Fast-Five (2011).mkv": "Theatrical"})
        self.assertEqual(plan["editions"], {})

    def test_current_label_is_read_from_the_name(self):
        labels = library.current_editions(BASE, self.roles)
        self.assertEqual(labels, {"Fast-Five (2011) - Version 2.mkv": "Version 2"})


class TestEditionPage(LibraryCase):

    def setUp(self):
        super().setUp()
        self.write(f"{BASE}/Fast-Five (2011).mkv", 100)
        self.write(f"{BASE}/Fast-Five (2011) - Extended cut.mkv", 99)
        self.config = patch.dict(app.config, {"WTF_CSRF_ENABLED": False, "LOGIN_DISABLED": True})
        self.config.start()
        self.busy = patch("arm.automation.health.busy", return_value=False)
        self.busy.start()
        self.length = patch("arm.automation.organize.duration_seconds", side_effect=lambda p: 7834.4)
        self.length.start()
        self.client = app.test_client()

    def tearDown(self):
        self.length.stop()
        self.busy.stop()
        self.config.stop()
        super().tearDown()

    def test_form_shows_labels_and_lengths_and_applies_them(self):
        page = self.client.get("/library/files/item", query_string={"id": BASE}).get_data(as_text=True)
        self.assertIn("2:10:34", page)
        self.assertIn('<option value="Extended cut" selected>', page)
        self.assertIn("Director&#39;s cut", page)
        form = {"id": BASE, "kind": "movie", "title": "Fast Five", "year": "2011",
                "file_0": "Fast-Five (2011).mkv", "role_0": "main", "edition_0": "Theatrical",
                "file_1": "Fast-Five (2011) - Extended cut.mkv", "role_1": "version",
                "edition_1": "other", "edition_text_1": "Extended edition", "action": "preview"}
        preview = self.client.post("/library/files/item", data=form).get_data(as_text=True)
        self.assertIn("Fast Five (2011) - Theatrical.mkv", preview)
        self.assertIn("Fast Five (2011) - Extended edition.mkv", preview)
        self.assertIn('value="Extended edition"', preview)  # the typed label survives the preview
        self.client.post("/library/files/item", data=dict(form, action="apply"))
        self.assertTrue(self.exists("movies/Fast Five (2011)/Fast Five (2011) - Theatrical.mkv"))
        self.assertTrue(self.exists("movies/Fast Five (2011)/Fast Five (2011) - Extended edition.mkv"))
        self.assertFalse(os.path.exists(os.path.join(self.lib, BASE)))


if __name__ == '__main__':
    unittest.main()
