"""
C13: drives listed by udev (the host's /sys) but not passed into the container are
ignored instead of showing as empty drive bays.
"""
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/opt/arm')
from arm.ui.settings import DriveUtils as drive_utils  # noqa: E402


def fake_device(node):
    device = MagicMock()
    device.device_node = node
    device.properties = {"ID_TYPE": "cd", "ID_CDROM": "1", "DEVNAME": node}
    return device


class TestPhantomDrives(unittest.TestCase):

    def search(self, present):
        context = MagicMock()
        context.list_devices.return_value = [fake_device("/dev/sr0"), fake_device("/dev/sr1")]
        with patch.object(drive_utils.pyudev, "Context", return_value=context), \
                patch.object(drive_utils.os.path, "exists", side_effect=lambda node: node in present), \
                patch.object(drive_utils.os, "access", return_value=True):
            return list(drive_utils.drives_search())

    def test_only_passed_in_drives_listed(self):
        """CHECK only drives whose device node exists in the container are listed"""
        self.assertEqual(len(self.search({"/dev/sr1"})), 1)

    def test_no_drives_passed_in(self):
        """CHECK a container with no drives lists none"""
        self.assertEqual(self.search(set()), [])

    def test_all_drives_passed_in(self):
        """CHECK the normal case still lists every drive"""
        self.assertEqual(len(self.search({"/dev/sr0", "/dev/sr1"})), 2)

    def test_unreadable_node_ignored(self):
        """CHECK a node ARM's user can't read is not usable"""
        with patch.object(drive_utils.os.path, "exists", return_value=True), \
                patch.object(drive_utils.os, "access", return_value=False):
            self.assertFalse(drive_utils.drive_node_usable("/dev/sr0"))


if __name__ == '__main__':
    unittest.main()
