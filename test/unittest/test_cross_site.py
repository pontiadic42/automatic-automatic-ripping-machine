"""
Upstream issue #1868: a link on another website must not be able to delete, abandon or eject.
"""
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
from arm.ui import app  # noqa: E402
from arm.ui.jobs import jobs  # noqa: E402


class TestCrossSite(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()

    def _get(self, url, **headers):
        with patch.dict(app.config, {"LOGIN_DISABLED": True}), \
                patch.object(jobs.ui_utils, "authenticated_state", return_value=True), \
                patch.object(jobs.json_api, "delete_job", return_value={"success": True}) as delete:
            response = self.client.get(url, headers=headers)
        return response, delete

    def test_delete_from_another_site_is_refused(self):
        response, delete = self._get("/json?mode=delete&job=1", **{"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(response.status_code, 403)
        delete.assert_not_called()

    def test_same_site_other_port_is_refused(self):
        response, delete = self._get("/json?mode=delete&job=1", **{"Sec-Fetch-Site": "same-site"})
        self.assertEqual(response.status_code, 403)
        delete.assert_not_called()

    def test_old_browser_referer_from_elsewhere_is_refused(self):
        response, delete = self._get("/json?mode=delete&job=1", Referer="https://evil.example/page")
        self.assertEqual(response.status_code, 403)
        delete.assert_not_called()

    def test_own_page_and_scripts_still_work(self):
        response, delete = self._get("/json?mode=delete&job=1", **{"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(response.status_code, 200)
        delete.assert_called_once()
        response, delete = self._get("/json?mode=delete&job=1", Referer="http://localhost/jobdetail")
        self.assertEqual(response.status_code, 200)
        response, delete = self._get("/json?mode=delete&job=1")
        self.assertEqual(response.status_code, 200)

    def test_reading_from_another_site_is_allowed(self):
        response, _ = self._get("/json?mode=joblist", **{"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(response.status_code, 200)

    def test_eject_link_and_post_from_another_site_are_refused(self):
        with patch.dict(app.config, {"LOGIN_DISABLED": True}):
            self.assertEqual(self.client.get("/drive/eject/1", headers={"Sec-Fetch-Site": "cross-site"}).status_code,
                             403)
            # A form post is refused too (by the CSRF token check or by this one)
            self.assertIn(self.client.post("/automation/settings",
                                           headers={"Sec-Fetch-Site": "cross-site"}).status_code, (400, 403))

    def test_phone_action_links_are_exempt(self):
        from arm.ui import cross_site
        with app.test_request_context("/automation/act?t=x", headers={"Sec-Fetch-Site": "cross-site"}):
            self.assertIsNone(cross_site.check_request())


if __name__ == '__main__':
    unittest.main()
