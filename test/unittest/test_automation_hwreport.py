"""
C18/C19/C23: hardware check page, encoder each job used, optional Blu-ray GPU decoding.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import hwcheck, hwreport  # noqa: E402
from arm.ui import app  # noqa: E402

QSV_LOG = """[00:32:03]    + encoder: H.264 (Intel QSV)
[00:32:03]    + encoder: AAC (libavcodec)
work: average encoding speed for job is 0.000000 fps
work: average encoding speed for job is 1255.561035 fps"""


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp, "arm.db"),
                                                 "HW_DECODE_BLURAY": True})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestEncoderUsed(Temp):

    def test_qsv(self):
        """CHECK QSV is reported as GPU with the average speed (scan pass ignored)"""
        used = hwcheck.encoder_used(QSV_LOG)
        self.assertEqual(used["label"], "QSV H.264 (GPU)")
        self.assertTrue(used["gpu"])
        self.assertEqual(used["fps"], 1256)

    def test_x264_and_none(self):
        """CHECK x264 is CPU, and a log without HandBrake gives None"""
        self.assertEqual(hwcheck.encoder_used("+ encoder: H.264 (libx264)")["label"], "x264 (CPU)")
        self.assertIsNone(hwcheck.encoder_used("MakeMKV only"))


class TestDecodeArgs(Temp):

    def job(self):
        return SimpleNamespace(disctype="bluray")

    def test_only_after_passing_test(self):
        """CHECK the decoding option is added only after a recent passing decode test"""
        args = "-e qsv_h264 --encoder-preset speed"
        self.assertEqual(hwreport.bluray_decode_args(self.job(), args), args)
        hwreport._save_decode({"ok": True, "flag": "--enable-hw-decoding qsv", "checked": time.time()})
        self.assertEqual(hwreport.bluray_decode_args(self.job(), args), args + " --enable-hw-decoding qsv")
        self.assertEqual(hwreport.bluray_decode_args(SimpleNamespace(disctype="dvd"), args), args)
        cfg.arm_config["HW_DECODE_BLURAY"] = False
        self.assertEqual(hwreport.bluray_decode_args(self.job(), args), args)

    def test_flag_by_version(self):
        """CHECK 1.9 uses --enable-qsv-decoding, 1.10+ --enable-hw-decoding qsv"""
        self.assertEqual(hwreport.decode_flag((1, 9, 2)), "--enable-qsv-decoding")
        self.assertEqual(hwreport.decode_flag((1, 11, 2)), "--enable-hw-decoding qsv")


class TestPage(Temp):

    def test_page_renders(self):
        """CHECK the hardware page renders without a GPU"""
        with patch.dict(app.config, {"LOGIN_DISABLED": True}):
            response = app.test_client().get("/automation/hardware")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Hardware check", response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
