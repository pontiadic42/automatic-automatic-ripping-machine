"""
C15: unfinalized MakeMKV files are caught after the rip, and can be repaired.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, '/opt/arm')
import arm.config.config as cfg  # noqa: E402
from arm.automation import mkvcheck  # noqa: E402
from arm.ripper import arm_ripper, makemkv  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def make_unfinalized(good, bad):
    """Copy a real MKV, replacing everything before its first Segment child with MakeMKV's marker"""
    offset = mkvcheck.data_offset(good)
    with open(good, "rb") as src:
        data = src.read()
    marker = mkvcheck.UNFINALIZED_TEXT.ljust(offset, b" ")[:offset]
    with open(bad, "wb") as out:
        out.write(marker + data[offset:])


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patch = patch.dict(cfg.arm_config, {"DBFILE": os.path.join(self.tmp, "arm.db"), "CHECK_UNFINALIZED": True})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path


class TestDetect(Temp):

    def test_header_states(self):
        """CHECK good, unfinalized, other and empty files are told apart"""
        self.assertEqual(mkvcheck.header_state(self.write("a.mkv", mkvcheck.EBML_MAGIC + b"\x00" * 10)), "ok")
        self.assertEqual(mkvcheck.header_state(self.write("b.mkv", mkvcheck.UNFINALIZED_TEXT + b"...")),
                         "unfinalized")
        self.assertEqual(mkvcheck.header_state(self.write("c.mkv", b"garbage")), "bad")
        self.assertEqual(mkvcheck.header_state(self.write("d.mkv", b"")), "empty")
        self.assertEqual([os.path.basename(p) for p in mkvcheck.unfinalized_files(self.tmp)],
                         ["b.mkv", "c.mkv", "d.mkv"])

    def test_rip_with_unfinalized_file_is_a_failure(self):
        """CHECK the ripper renames unfinalized files and raises a MakeMKV error (so it retries)"""
        self.write("B1_t00.mkv", mkvcheck.EBML_MAGIC + b"\x00")
        self.write("B1_t01.mkv", mkvcheck.UNFINALIZED_TEXT)
        with self.assertRaises(makemkv.MakeMkvRuntimeError) as error:
            arm_ripper.check_finalized(self.tmp)
        self.assertIn("unfinalized", str(error.exception))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "B1_t01.mkv.unfinalized")))
        self.assertEqual(arm_ripper.check_finalized(self.tmp), self.tmp)  # the rest is fine now

    def test_switched_off(self):
        """CHECK CHECK_UNFINALIZED off skips the check"""
        cfg.arm_config["CHECK_UNFINALIZED"] = False
        self.write("B1_t01.mkv", mkvcheck.UNFINALIZED_TEXT)
        self.assertEqual(arm_ripper.check_finalized(self.tmp), self.tmp)


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class TestRepair(Temp):

    def test_repair_makes_a_readable_file(self):
        """CHECK a real MKV with MakeMKV's marker instead of a header can be read again after repair"""
        good = os.path.join(self.tmp, "good.mkv")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25", "-t", "2",
                        "-c:v", "mpeg2video", good], check=True, timeout=60)
        bad = os.path.join(self.tmp, "bad.mkv")
        make_unfinalized(good, bad)
        self.assertEqual(mkvcheck.header_state(bad), "unfinalized")
        fixed = os.path.join(self.tmp, "fixed.mkv")
        mkvcheck.repair(bad, fixed)
        self.assertEqual(mkvcheck.header_state(fixed), "ok")
        frames = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                                 "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", fixed],
                                capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(frames, "50")
        self.assertEqual(mkvcheck.header_state(bad), "unfinalized")  # original unchanged


if __name__ == '__main__':
    unittest.main()
