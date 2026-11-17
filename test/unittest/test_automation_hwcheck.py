"""
C12: hardware encoder checks. A real test encode decides, a failed check before ripping
switches the job to the CPU, and a failed hardware transcode is retried on the CPU.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import hwcheck, notify, store  # noqa: E402
from arm.ripper import handbrake  # noqa: E402

QSV_ARGS = '--subtitle scan -F -e qsv_h264 --encoder-preset speed --enable-qsv-decoding'


class TempStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp, "arm.db"),
                                                 "NTFY_SERVER": "", "NTFY_TOPIC": "",
                                                 "HW_ENCODER_CHECK": True, "HW_ENCODER_FALLBACK": True})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


def make_job(args=QSV_ARGS, disctype="dvd"):
    return SimpleNamespace(job_id=7, title="Test", disctype=disctype,
                           config=SimpleNamespace(HB_ARGS_DVD=args, HB_ARGS_BD=args, SKIP_TRANSCODE=False))


class TestArgs(unittest.TestCase):

    def test_encoder_in(self):
        """CHECK the encoder is read from -e and --encoder"""
        self.assertEqual(hwcheck.encoder_in(QSV_ARGS), "qsv_h264")
        self.assertEqual(hwcheck.encoder_in("--encoder=nvenc_h265 -q 22"), "nvenc_h265")
        self.assertIsNone(hwcheck.encoder_in("--subtitle scan -F"))
        self.assertTrue(hwcheck.is_hardware("qsv_h265"))
        self.assertFalse(hwcheck.is_hardware("x264"))

    def test_software_args(self):
        """CHECK the hardware encoder and its options are swapped for CPU ones"""
        self.assertEqual(hwcheck.software_args(QSV_ARGS), "--subtitle scan -F -e x264 --encoder-preset veryfast")
        self.assertEqual(hwcheck.software_args("-e qsv_h265 --enable-hw-decoding qsv --qsv-adapter 1"), "-e x265")
        self.assertEqual(hwcheck.software_args("-e x264 --encoder-preset slow"), "-e x264 --encoder-preset veryfast")

    def test_explain(self):
        """CHECK known HandBrake errors get a cause and a fix"""
        cause, fix = hwcheck.explain("[QSV @ 0x1] libva: This version of libva doesn't support ... VA-API 1.15.0")
        self.assertIn("old libva", cause)
        cause, _fix = hwcheck.explain("ERROR: encqsvInit: qsv is not available on the system")
        self.assertIn("No usable GPU", cause)

    def test_used_hardware(self):
        """CHECK the log must show the QSV encoder running"""
        self.assertTrue(hwcheck.used_hardware("qsv_h264", "qsv_enc_init: using 'hardware (1) via VAAPI'"))
        self.assertTrue(hwcheck.used_hardware("qsv_h264", "encoder: H.264 (Intel QSV)"))
        self.assertFalse(hwcheck.used_hardware("qsv_h264", "x264 [info]: profile High"))


class TestPrecheck(TempStore):

    def run_precheck(self, ok):
        result = {"ok": ok, "encoder": "qsv_h264", "seconds": 2.0, "cause": "No usable GPU", "fix": "Pass it in"}
        with patch.object(hwcheck, "cached_test", return_value=result) as test, \
                patch.object(notify, "send") as send, patch.object(notify, "in_app"):
            hwcheck.precheck(make_job())
        return test, send

    def test_working_encoder_keeps_args(self):
        """CHECK a passing test changes nothing"""
        _test, send = self.run_precheck(True)
        send.assert_not_called()
        self.assertFalse(hwcheck.planned_fallback(make_job()))
        self.assertEqual(hwcheck.adjust_args(make_job(), QSV_ARGS), QSV_ARGS)

    def test_failed_encoder_falls_back_and_warns(self):
        """CHECK a failing test before ripping warns and switches this job to the CPU"""
        _test, send = self.run_precheck(False)
        send.assert_called_once()
        self.assertTrue(hwcheck.planned_fallback(make_job()))
        self.assertIn("-e x264", hwcheck.adjust_args(make_job(), QSV_ARGS))

    def test_no_fallback_only_warns(self):
        """CHECK with the fallback off the job keeps its arguments"""
        cfg.arm_config["HW_ENCODER_FALLBACK"] = False
        _test, send = self.run_precheck(False)
        send.assert_called_once()
        self.assertFalse(hwcheck.planned_fallback(make_job()))

    def test_cpu_encoder_not_tested(self):
        """CHECK nothing runs when HB_ARGS don't ask for a GPU encoder, or the check is off"""
        with patch.object(hwcheck, "cached_test") as test:
            self.assertIsNone(hwcheck.precheck(make_job("--subtitle scan -F")))
            cfg.arm_config["HW_ENCODER_CHECK"] = False
            self.assertIsNone(hwcheck.precheck(make_job()))
        test.assert_not_called()

    def test_cache(self):
        """CHECK the test result is cached and reused"""
        with patch.object(hwcheck, "test_encode", return_value={"ok": True, "checked": 9e12}) as test:
            hwcheck.cached_test("qsv_h264")
            hwcheck.cached_test("qsv_h264")
        self.assertEqual(test.call_count, 1)
        self.assertTrue(hwcheck.last_result("qsv_h264")["ok"])


class TestTranscodeRetry(TempStore):

    def test_failed_hardware_transcode_retried_on_cpu(self):
        """CHECK HandBrake failing with qsv_h264 is run again with x264"""
        calls = []

        def fake_check_output(cmd, shell):
            calls.append(cmd)
            if "qsv_h264" in cmd:
                raise subprocess.CalledProcessError(3, cmd, output=b"")
            return b""
        cmd = f"nice HandBrakeCLI -i in -o out {QSV_ARGS} >> log 2>&1"
        with patch.object(handbrake.subprocess, "check_output", side_effect=fake_check_output), \
                patch.object(notify, "send"), patch.object(notify, "in_app"):
            handbrake.run_handbrake_command(cmd, job=make_job())
        self.assertEqual(len(calls), 2)
        self.assertIn("-e x264", calls[1])
        self.assertIn(">> log 2>&1", calls[1])
        self.assertEqual(store.load_job(7)["hw_fallback"]["when"], "transcode")

    def test_cpu_failure_not_retried(self):
        """CHECK a CPU encode that fails is not retried"""
        with patch.object(handbrake.subprocess, "check_output",
                          side_effect=subprocess.CalledProcessError(1, "x", output=b"")) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                handbrake.run_handbrake_command("HandBrakeCLI -e x264", job=make_job())
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
