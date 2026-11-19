"""
C39: the web UI points at A2RM, not ARM's project, and doesn't check ARM's GitHub for "updates".
"""
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/opt/arm')
from arm.ui import app  # noqa: E402
from arm.ripper import music_brainz  # noqa: E402

A2RM = "github.com/pontiadic42/automatic-automatic-ripping-machine"


class TestBranding(unittest.TestCase):

    def test_footer_and_help_point_at_a2rm(self):
        with app.test_request_context("/"):
            footer = app.jinja_env.get_template("base.html").render()
            help_tab = app.jinja_env.get_template("settings/help.html").render()
        self.assertIn(A2RM, footer)
        self.assertIn(A2RM, help_tab)
        self.assertIn("ARM-vs-A2RM.md", help_tab)
        self.assertNotIn("discord.gg", help_tab)

    def test_settings_page_doesnt_ask_arms_github(self):
        from arm.ui.settings import settings as settings_routes
        with patch.dict(app.config, {"LOGIN_DISABLED": True}), \
                patch.object(settings_routes.ui_utils, "git_check_version", return_value=["2.24.4", ""]), \
                patch.object(settings_routes.ui_utils, "git_check_updates") as updates, \
                patch.object(settings_routes, "check_hw_transcode_support", return_value={}), \
                patch.object(settings_routes, "drive_utils"):
            html = app.test_client().get("/settings").get_data(as_text=True)
        updates.assert_not_called()
        self.assertIn("A2RM, based on ARM <code>2.24.4</code>", html)
        self.assertNotIn("Update available", html)

    def test_musicbrainz_sees_a2rm(self):
        self.assertIn(A2RM, music_brainz.A2RM_URL)
        with patch.object(music_brainz.mb, "set_useragent") as agent, \
                patch.object(music_brainz.mb, "get_releases_by_discid", side_effect=RuntimeError("stop")):
            try:
                music_brainz.get_disc_info(MagicMock(arm_version="2.24.4"), "discid")
            except RuntimeError:
                pass  # only the user agent matters here
        self.assertEqual(agent.call_args.kwargs["app"], "a2rm")
        self.assertEqual(agent.call_args.kwargs["contact"], music_brainz.A2RM_URL)


if __name__ == '__main__':
    unittest.main()
