"""
C4/C5: language settings are drop-downs (one language, or several) and sit together in
one "Languages" group on Settings > Automation.
"""
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import settings, titles  # noqa: E402
from arm.ui import app  # noqa: E402


class TestLanguageSettings(unittest.TestCase):

    def test_grouped_together(self):
        """CHECK every language setting is in the Languages group"""
        for key in ("PREFERRED_LANGUAGE", "KEEP_OTHER_LANGUAGE_VERSIONS", "TRIM_TRACKS",
                    "KEEP_AUDIO_LANGUAGES", "KEEP_SUBTITLE_LANGUAGES"):
            self.assertEqual(settings.BY_KEY[key].group, "languages", key)
        self.assertIn("languages", [group for group, _title in settings.GROUPS])

    def test_coerce(self):
        """CHECK form lists and bibliographic codes become clean ISO 639-2/T codes"""
        langs = settings.BY_KEY["KEEP_AUDIO_LANGUAGES"]
        self.assertEqual(settings.coerce(langs, ["eng", "fre", "eng"]), "eng,fra")
        self.assertEqual(settings.coerce(langs, " ENG , ger "), "eng,deu")
        single = settings.BY_KEY["PREFERRED_LANGUAGE"]
        self.assertEqual(settings.coerce(single, "fre"), "fra")
        self.assertEqual(settings.coerce(single, "eng,fra"), "eng")

    def test_choices_keep_unknown_codes_and_put_chosen_first(self):
        """CHECK the chosen languages come first and a code not in the list isn't lost"""
        choices = settings.language_choices("jpn,xyz")
        self.assertEqual(choices[0], ("jpn", "Japanese (jpn)"))
        self.assertEqual(choices[1], ("xyz", "xyz"))
        self.assertIn(("eng", "English (eng)"), choices)

    def test_preferred_matches_bibliographic_code(self):
        """CHECK a title MakeMKV tags 'fre' counts as the preferred 'fra'"""
        bucket = [SimpleNamespace(tid=0, language="eng"), SimpleNamespace(tid=1, language="fre")]
        chosen, guessed = titles._pick_version(bucket, "fra")
        self.assertEqual(chosen.tid, 1)
        self.assertFalse(guessed)


class TestSettingsPage(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()

    def tearDown(self):
        app.config["WTF_CSRF_ENABLED"] = True
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_page_shows_drop_downs(self):
        """CHECK the page has a single select and multi-selects with language names"""
        with patch.dict(app.config, {"LOGIN_DISABLED": True}), \
                patch.dict(cfg.arm_config, {"KEEP_AUDIO_LANGUAGES": "eng,fra",
                                            "DBFILE": os.path.join(self.tmp, "a.db")}):
            html = self.client.get("/automation/settings").get_data(as_text=True)
        self.assertIn('<select id="PREFERRED_LANGUAGE" name="PREFERRED_LANGUAGE"', html)
        self.assertIn('name="KEEP_AUDIO_LANGUAGES" class="custom-select arm-auto-multi" multiple', html)
        self.assertIn('<option value="fra" selected>French (fra)</option>', html)
        self.assertIn('id="g-languages"', html)

    def test_save_multi_select(self):
        """CHECK several picked languages are saved comma-separated"""
        config_path = os.path.join(self.tmp, "arm.yaml")
        shutil.copy(cfg.arm_config_path, config_path)
        form = {"KEEP_AUDIO_LANGUAGES": ["eng", "jpn"], "PREFERRED_LANGUAGE": "jpn"}
        with patch.object(cfg, "arm_config_path", config_path), patch.dict(app.config, {"LOGIN_DISABLED": True}), \
                patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp, "a.db")}):
            self.assertEqual(self.client.post("/automation/settings", data=form).status_code, 302)
        saved = cfg._load_config(config_path)
        self.assertEqual(saved["KEEP_AUDIO_LANGUAGES"], "eng,jpn")
        self.assertEqual(saved["PREFERRED_LANGUAGE"], "jpn")


if __name__ == '__main__':
    unittest.main()
