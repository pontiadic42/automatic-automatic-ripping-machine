"""
UI consolidation (C36-C38): one job page (Identify, Files, Disc, Details), Library and Settings as tab bars,
old pages redirecting, and each Settings tab saving only its own A2RM settings.
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
from arm.automation import library  # noqa: E402
from arm.ui import app  # noqa: E402
import arm.ui.jobs.jobs as jobs_routes  # noqa: E402


class Case(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lib = os.path.join(self.tmp, "completed")
        for root in library.ROOTS:
            os.makedirs(os.path.join(self.lib, root))
        config = {"DBFILE": os.path.join(self.tmp, "arm.db"), "COMPLETED_PATH": self.lib, "WRITE_NFO": False,
                  "JELLYFIN_API_KEY": ""}
        self.patches = [patch.dict(cfg.arm_config, config),
                        patch.dict(app.config, {"LOGIN_DISABLED": True, "WTF_CSRF_ENABLED": False})]
        for p in self.patches:
            p.start()
        self.client = app.test_client()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestOldPagesRedirect(Case):

    def test_title_search_opens_identify(self):
        reply = self.client.get("/titlesearch?job_id=5&title=Date+Night&year=2010&type=movie")
        self.assertEqual(reply.status_code, 302)
        self.assertIn("/jobdetail?job_id=5", reply.location)
        self.assertIn("q=Date+Night", reply.location)
        self.assertTrue(reply.location.endswith("#identify"))
        self.assertTrue(self.client.get("/titlesearch?job_id=5").location.endswith("job_id=5#identify"))

    def test_custom_title_without_a_title_opens_identify(self):
        query = MagicMock()
        query.get.return_value = SimpleNamespace(job_id=5, title="X", year="2000")
        with patch.object(jobs_routes.Job, "query", query), patch.object(jobs_routes.ui_utils, "job_id_validator"):
            reply = self.client.get("/customTitle?job_id=5")
        self.assertTrue(reply.location.endswith("/jobdetail?job_id=5#identify"))

    def test_rip_settings_and_active_rips(self):
        self.assertTrue(self.client.get("/changeparams?config_id=7").location.endswith("job_id=7#rip-settings"))
        self.assertEqual(self.client.get("/activerips").status_code, 302)

    def test_search_without_title(self):
        self.assertEqual(self.client.get("/identify/search?job_id=5").get_json()["results"], [])

    def test_search_tries_without_the_year(self):
        found = {"Search": [{"Title": "Date Night", "Type": "movie", "Year": "2010", "imdbID": "tt1279935",
                             "Poster": "N/A"}]}
        with patch.object(jobs_routes.ui_utils, "metadata_selector", side_effect=[{"Error": "x"}, found]) as sel:
            data = self.client.get("/identify/search?title=Date+Night&year=2011&type=movie").get_json()
        self.assertEqual(sel.call_count, 2)
        self.assertIn("without the year", data["message"])
        self.assertEqual(data["results"][0]["poster"], "")  # "N/A" isn't a picture


class TestSettingsTabs(Case):

    def setUp(self):
        super().setUp()
        self.config_path = os.path.join(self.tmp, "arm.yaml")
        shutil.copy(cfg.arm_config_path, self.config_path)
        with open(self.config_path, "a") as handle:
            handle.write("\nKEEP_ALTERNATE_CUTS: true\nHW_ENCODER_CHECK: true\n")
        self.patches.append(patch.object(cfg, "arm_config_path", self.config_path))
        self.patches[-1].start()

    def test_a_tab_saves_only_its_groups(self):
        """CHECK saving the Notifications tab doesn't untick checkboxes on the A2RM or Hardware tabs"""
        with patch.dict(cfg.arm_config, {}):
            reply = self.client.post("/automation/settings", data={
                "_groups": "notify", "_next": "/automation/settings?section=notifications",
                "NTFY_SERVER": "https://ntfy.example", "NTFY_TOPIC": "arm", "NTFY_TOKEN": ""})
        self.assertTrue(reply.location.endswith("/automation/settings?section=notifications"))
        saved = cfg._load_config(self.config_path)
        self.assertEqual(saved["NTFY_SERVER"], "https://ntfy.example")
        self.assertIs(saved["KEEP_ALTERNATE_CUTS"], True)
        self.assertIs(saved["HW_ENCODER_CHECK"], True)

    def test_hardware_tab_saves_its_group(self):
        with patch.dict(cfg.arm_config, {}):
            reply = self.client.post("/automation/settings", data={"_groups": "hardware",
                                                                   "_next": "/automation/hardware"})
        self.assertTrue(reply.location.endswith("/automation/hardware"))
        saved = cfg._load_config(self.config_path)
        self.assertIs(saved["HW_ENCODER_CHECK"], False)       # unticked on the Hardware tab
        self.assertIs(saved["KEEP_ALTERNATE_CUTS"], True)     # A2RM tab untouched

    def test_unknown_next_page_is_ignored(self):
        with patch.dict(cfg.arm_config, {}):
            reply = self.client.post("/automation/settings", data={"_groups": "notify",
                                                                   "_next": "https://evil.example"})
        self.assertTrue(reply.location.endswith("/automation/settings"))

    def test_tabs_show_their_groups(self):
        a2rm = self.client.get("/automation/settings").get_data(as_text=True)
        notes = self.client.get("/automation/settings?section=notifications").get_data(as_text=True)
        self.assertIn('class="arm-tabs"', a2rm)
        self.assertIn('name="TITLE_ANALYSIS"', a2rm)
        self.assertNotIn('name="NTFY_SERVER"', a2rm)
        self.assertNotIn('name="HW_ENCODER_CHECK"', a2rm)
        self.assertIn('name="NTFY_SERVER"', notes)
        self.assertIn('id="appriseCfg"', notes)
        self.assertIn('value="notify"', notes)

    def test_old_settings_tabs(self):
        with patch("arm.ui.settings.settings.ui_utils.git_check_version", return_value=["1", "1"]), \
                patch("arm.ui.settings.settings.ui_utils.get_git_revision_hash", return_value="abc"), \
                patch("arm.ui.settings.settings.ui_utils.git_check_updates", return_value=False), \
                patch("arm.ui.settings.settings.check_hw_transcode_support", return_value=""), \
                patch("arm.ui.settings.settings.drive_utils"):
            music = self.client.get("/settings?tab=music").get_data(as_text=True)
            fallback = self.client.get("/settings?tab=nope").get_data(as_text=True)
        self.assertIn('const panes = ["abcde"]', music)
        self.assertIn('const panes = ["home", "system"]', fallback)
        self.assertNotIn('id="appriseCfg"', music)  # Apprise moved to Settings > Notifications


class TestJobPage(Case):

    def job(self, **extra):
        values = dict(job_id=12, title="Date Night", year="2010", video_type="movie", status="success",
                      manual_mode=False, manual_start=True, path=None, poster_url="", background=None, plot="",
                      label="DATE_NIGHT", disctype="dvd", logfile="DATE_NIGHT.log", title_manual=None,
                      imdb_id="tt1279935", pid_hash=0,
                      config=SimpleNamespace(CONFIG_ID=3, RIPMETHOD="mkv", DISCTYPE="dvd", MAINFEATURE=False,
                                             MINLENGTH=600, MAXLENGTH=99999, HB_PRESET_DVD="", HB_ARGS_DVD="",
                                             HB_PRESET_BD="", HB_ARGS_BD=""))
        values.update(extra)
        job = SimpleNamespace(**values)
        job.tracks = MagicMock()
        job.tracks.all.return_value = []
        return job

    def get(self, job, url="/jobdetail?job_id=12"):
        query = MagicMock()
        query.get.return_value = job
        with patch.object(jobs_routes.Job, "query", query), \
                patch.object(jobs_routes.ui_utils, "metadata_selector", return_value={}):
            return self.client.get(url).get_data(as_text=True)

    def test_sections_and_controls(self):
        html = self.get(self.job())
        for needle in ['id="identify"', 'id="idSearch"', 'action="/customTitle"', 'id="details"',
                       'id="jobtable"', 'id="tracktable"', 'id="toggle-time-format"', 'id="changeParams"',
                       'name="RIPMETHOD"', 'name="MINLENGTH"', 'id="plot"', 'id="posterClick"']:
            self.assertIn(needle, html)

    def test_search_from_the_address(self):
        html = self.get(self.job(), "/jobdetail?job_id=12&q=Date+Night&type=series&pick=tt1279935")
        self.assertIn('search({job_id: jobId, title: "Date Night"', html)
        self.assertIn('showPick("tt1279935")', html)

    def test_music_has_no_identify(self):
        self.assertNotIn('id="identify"', self.get(self.job(video_type="Music")))

    def test_files_editor_for_a_library_folder(self):
        folder = os.path.join(self.lib, "movies", "Date Night (2010)")
        os.makedirs(folder)
        with open(os.path.join(folder, "Date Night (2010).mkv"), "wb") as handle:
            handle.write(b"x" * 100)
        with patch.object(library, "tracked_jobs", return_value={"movies/Date Night (2010)": 12}):
            html = self.get(self.job(path=folder))
        self.assertIn('id="files"', html)
        self.assertIn('class="arm-lib-editor" data-back="12"', html)
        self.assertIn('name="role_0"', html)


class TestLibraryItem(Case):

    def setUp(self):
        super().setUp()
        folder = os.path.join(self.lib, "movies", "Flywheel (2003)")
        os.makedirs(folder)
        with open(os.path.join(folder, "Flywheel (2003).mkv"), "wb") as handle:
            handle.write(b"x" * 100)

    def test_job_item_opens_the_job_page(self):
        with patch.object(library, "tracked_jobs", return_value={"movies/Flywheel (2003)": 30}):
            reply = self.client.get("/library/files/item", query_string={"id": "movies/Flywheel (2003)"})
            here = self.client.get("/library/files/item", query_string={"id": "movies/Flywheel (2003)", "here": "1"})
        self.assertTrue(reply.location.endswith("/jobdetail?job_id=30#files"))
        self.assertEqual(here.status_code, 200)

    def test_preview_from_the_job_page_returns_the_editor(self):
        with patch.object(library, "tracked_jobs", return_value={"movies/Flywheel (2003)": 30}):
            html = self.client.post("/library/files/item", data={
                "id": "movies/Flywheel (2003)", "back": "30", "partial": "1", "action": "preview", "kind": "movie",
                "title": "Fly Wheel", "year": "2003", "file_0": "Flywheel (2003).mkv", "role_0": "main"})
            html = html.get_data(as_text=True)
        self.assertNotIn("<html", html)
        self.assertIn('data-back="30"', html)
        self.assertIn("Fly Wheel (2003)", html)

    def test_untracked_item_page_has_library_tabs(self):
        with patch.object(library, "tracked_jobs", return_value={}):
            html = self.client.get("/library/files/item", query_string={"id": "movies/Flywheel (2003)"})
            html = html.get_data(as_text=True)
        self.assertIn('class="arm-tabs"', html)
        self.assertIn("lib-search", html)


if __name__ == '__main__':
    unittest.main()
