"""
Library health check (HEALTH_CHECK_DAYS): every few days, from the maintenance run and never while
a disc is ripping or transcoding, look at every video file under COMPLETED_PATH:

- empty files; .mkv files that don't start with a Matroska header (MakeMKV's "unfinalized"
  files, C15);
- ffprobe can't read the file, or it has no video stream;
- optional (HEALTH_CHECK_DECODE): decoding 5 seconds from the middle gives errors;
- the main file of a movie ARM filed is far from TMDb's runtime;
- files of the same size and length in different places (probable duplicates).

Read-only: nothing is moved or deleted. Results are on /automation/health and in the daily summary.
"""
import collections
import datetime
import json
import logging
import os
import subprocess
import time

import arm.config.config as cfg
from arm.automation import mkvcheck, settings, store

VIDEO = (".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts")
ACTIVE = ("ripping", "transcoding")
DUPLICATE_MIN_BYTES = 100 * 1024 * 1024


def probe(path):
    """{'duration': seconds or None, 'video': n video streams} or None if ffprobe can't read it"""
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                                 "-of", "json", path], capture_output=True, text=True, timeout=120)
        data = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if result.returncode != 0 or "format" not in data:
        return None
    try:
        duration = float(data["format"].get("duration"))
    except (TypeError, ValueError):
        duration = None
    video = sum(1 for s in data.get("streams") or [] if s.get("codec_type") == "video")
    return {"duration": duration, "video": video}


def decode_errors(path, duration):
    """Decode 5 s from the middle; the first error line, or None"""
    start = int(duration / 2) if duration else 60
    try:
        result = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", "5", "-i", path, "-f", "null", "-"],
                                capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)
    lines = [line for line in (result.stderr or "").splitlines() if line.strip()]
    if result.returncode != 0 or lines:
        return (lines[0] if lines else f"ffmpeg exit {result.returncode}")[:200]
    return None


def check_file(path, decode=False):
    """List of problems with one file (empty = fine) and its probe result"""
    try:
        size = os.path.getsize(path)
    except OSError as error:
        return [f"can't read it: {error.strerror}"], None
    if size == 0:
        return ["empty file (0 bytes)"], None
    if path.lower().endswith(".mkv"):
        state = mkvcheck.header_state(path)
        if state == "unfinalized":
            return ["unfinalized MakeMKV file (repair with scripts/a2rm-repair-mkv.py)"], None
        if state != "ok":
            return ["doesn't start with a Matroska header"], None
    info = probe(path)
    if info is None:
        return ["ffprobe can't read it"], None
    problems = []
    if not info["video"]:
        problems.append("no video stream")
    if decode and info["video"]:
        error = decode_errors(path, info["duration"])
        if error:
            problems.append(f"decode errors: {error}")
    return problems, info


def library_files(root=None):
    root = root or str(cfg.arm_config.get("COMPLETED_PATH") or "")
    found = []
    for folder, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(VIDEO) and not name.startswith("."):
                found.append(os.path.join(folder, name))
    return sorted(found)


def expected_runtimes():
    """{main file path: (TMDb minutes, job id)} for movies ARM filed"""
    from arm.automation import tmdb
    from arm.models.job import Job
    result = {}
    folder = os.path.join(store.base_dir(), "jobs")
    for name in os.listdir(folder):
        if not name.endswith(".json"):
            continue
        try:
            job_id = int(name[:-5])
        except ValueError:
            continue
        mains = [f["file"] for f in store.load_job(job_id).get("files") or [] if f.get("role") == "main"]
        if not mains:
            continue
        job = Job.query.get(job_id)
        if job is None or job.video_type != "movie" or not job.imdb_id:
            continue
        minutes = tmdb.runtime_minutes(job.imdb_id)
        if minutes:
            for path in mains:
                result[path] = (minutes, job_id)
    return result


def busy():
    from arm.models.job import Job
    return Job.query.filter(Job.status.in_(ACTIVE)).count() > 0


def run(root=None, decode=None, runtimes=None):
    """Check the library; saves and returns the result"""
    decode = settings.get("HEALTH_CHECK_DECODE") if decode is None else decode
    started = time.time()
    files = library_files(root)
    if runtimes is None:
        try:
            runtimes = expected_runtimes()
        except Exception as error:  # noqa: BLE001 - optional part
            logging.info(f"Health check: TMDb runtimes not used: {error}")
            runtimes = {}
    problems = []
    seen = collections.defaultdict(list)
    for path in files:
        issues, info = check_file(path, decode)
        if info and info["duration"]:
            expected = runtimes.get(path)
            minutes = info["duration"] / 60
            if expected and abs(minutes - expected[0]) > max(10, 0.15 * expected[0]):
                issues.append(f"{minutes:.0f} min long, TMDb says {expected[0]} min (job {expected[1]})")
            size = os.path.getsize(path)
            if size >= DUPLICATE_MIN_BYTES:
                seen[(size, round(info["duration"]))].append(path)
        if issues:
            problems.append({"path": path, "problems": issues})
    for paths in seen.values():
        if len(paths) > 1:
            for path in paths:
                others = ", ".join(os.path.basename(p) for p in paths if p != path)
                entry = next((p for p in problems if p["path"] == path), None)
                if entry is None:
                    entry = {"path": path, "problems": []}
                    problems.append(entry)
                entry["problems"].append(f"same size and length as {others} (duplicate?)")
    result = {"checked": datetime.datetime.now().isoformat(timespec="seconds"), "files": len(files),
              "decode": bool(decode), "seconds": round(time.time() - started), "problems": problems}
    store.update_state(health=result)
    logging.info(f"Library health check: {len(files)} files, {len(problems)} with problems")
    return result


def due(local):
    days = settings.get("HEALTH_CHECK_DAYS")
    if days <= 0:
        return False
    hours, minutes = (int(part) for part in settings.get("HEALTH_CHECK_TIME").split(":"))
    if local < local.replace(hour=hours, minute=minutes, second=0, microsecond=0):
        return False
    last = store.load_state().get("last_health")
    if not last:
        return True
    try:
        return (local.date() - datetime.date.fromisoformat(last)).days >= days
    except ValueError:
        return True


def scheduled(local):
    """From the maintenance tick: run when due and nothing is ripping or transcoding"""
    if not due(local):
        return None
    if busy():
        logging.info("Library health check due, but a job is ripping or transcoding; trying again later")
        return None
    result = run()
    store.update_state(last_health=local.date().isoformat())
    return result


def summary_line():
    """For the daily summary: problems found by a check in the last day, or None"""
    result = store.load_state().get("health") or {}
    if not result.get("problems") or result.get("reported") == result.get("checked"):
        return None
    store.update_state(health=dict(result, reported=result["checked"]))
    return f"Library check: {len(result['problems'])} of {result['files']} files have problems (see /automation/health)"
