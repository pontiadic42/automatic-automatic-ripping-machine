"""
Log database transactions that stay open for a long time.

SQLite allows one writer, and in its default journal mode an open read transaction also
stops other processes from committing. A transaction left open for minutes (a web page
or a ripper step that never commits) makes every other process's write fail with
"database is locked". This logs each such transaction when it ends, with the process
and, for the web UI, the page that held it, so the culprit shows up in the logs.
"""
import logging
import os
import sys
import time

from sqlalchemy import event
from sqlalchemy.engine import Engine

SLOW_SECONDS = 20
_installed = False


def _who():
    try:
        from flask import has_request_context, request
        if has_request_context():
            return f"web UI request {request.method} {request.path}"
    except Exception:  # noqa: BLE001
        pass
    return f"{os.path.basename(sys.argv[0]) or 'python'} (pid {os.getpid()})"


def _begin(conn):
    conn.info["a2rm_tx_start"] = time.monotonic()
    conn.info["a2rm_tx_who"] = _who()


def _end(conn):
    started = conn.info.pop("a2rm_tx_start", None)
    who = conn.info.pop("a2rm_tx_who", "?")
    if started is None:
        return
    held = time.monotonic() - started
    if held >= SLOW_SECONDS:
        logging.warning(f"Database transaction held for {held:.0f} s by {who}; "
                        "other processes can't save while it is open")


def install():
    """Attach the listeners once per process"""
    global _installed
    if _installed:
        return
    event.listen(Engine, "begin", _begin)
    event.listen(Engine, "commit", _end)
    event.listen(Engine, "rollback", _end)
    _installed = True
