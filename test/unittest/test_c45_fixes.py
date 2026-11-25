"""
C45: bugs found while taking the GitHub screenshots (the episode-number fix is in test_automation_tv_season).
"""
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, '/opt/arm')
from arm.automation import health  # noqa: E402

STATIC = os.path.join(os.path.dirname(health.__file__), "..", "ui", "static", "css")
JOB_PAGE = os.path.join(os.path.dirname(health.__file__), "..", "ui", "jobs", "templates", "jobdetail.html")


def selectors(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for block in re.findall(r"([^{}]+)\{[^{}]*\}", css):
        for part in block.split(","):
            # skip declarations around a nested rule (dark-mode.css nests .close in .toast-header)
            if part.strip() and not part.strip().startswith("@") and ";" not in part:
                yield part.strip()


class TestC45(unittest.TestCase):

    def test_health_check_skips_the_trash(self):
        with tempfile.TemporaryDirectory() as root:
            for rel in ("movies/A (2001)/A (2001).mkv", ".a2rm-trash/20261120/tv/B/B S01E01.mkv"):
                os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
                open(os.path.join(root, rel), "w").close()
            found = [os.path.relpath(p, root).replace("\\", "/") for p in health.library_files(root)]
        self.assertEqual(found, ["movies/A (2001)/A (2001).mkv"])

    def test_dark_mode_rules_only_apply_in_dark_mode(self):
        with open(os.path.join(STATIC, "dark-mode.css")) as handle:
            loose = [s for s in selectors(handle.read()) if "data-theme" not in s and s not in (":root",)]
        self.assertEqual(loose, [])

    def test_arms_stacked_tables_leave_a2rms_tables_alone(self):
        with open(os.path.join(STATIC, "job_details.css")) as handle:
            css = handle.read()
        media = css[css.index("@media"):]
        inner = media[media.index("{") + 1:]
        self.assertTrue(all(s.startswith("table.table-striped") for s in selectors(inner)))

    def test_job_page_gives_the_editor_its_label_list(self):
        with open(JOB_PAGE) as handle:
            self.assertIn("editions=editor.editions", handle.read())


if __name__ == '__main__':
    unittest.main()
