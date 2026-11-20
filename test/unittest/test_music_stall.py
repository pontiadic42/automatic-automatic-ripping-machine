"""Upstream #1910: a music rip whose log stops growing (cdparanoia stuck on a scratch) is stopped."""
import subprocess
import time

import pytest


@pytest.fixture
def run(tmp_path, monkeypatch):
    import arm.config.config as cfg
    from arm.ripper import utils

    log_path = tmp_path / "music.log"

    def go(command, stall):
        monkeypatch.setitem(cfg.arm_config, "MUSIC_STALL_TIMEOUT", stall)
        with open(log_path, "ab") as log:
            return utils._run_abcde(command, log, str(log_path), poll=0.2)
    return go


def test_a_silent_rip_is_stopped(run):
    start = time.time()
    error = run(["sleep", "30"], 1)
    assert "stalled" in error
    assert time.time() - start < 10


def test_a_rip_that_keeps_writing_finishes(run):
    assert run(["sh", "-c", "for i in 1 2 3 4 5 6; do echo track $i; sleep 0.3; done"], 1) is None


def test_zero_turns_the_watchdog_off_and_failures_still_raise(run):
    assert run(["sleep", "1"], 0) is None
    with pytest.raises(subprocess.CalledProcessError):
        run(["sh", "-c", "exit 3"], 1)
