"""
The points where ARM's ripper calls into automation. Each hook is safe to call when its
feature is switched off, and an unexpected error in an optional step is logged rather
than failing the rip (except where stopping is the point: skip, no disk space).
"""
import logging

from arm.automation import (confidence, hwcheck, jellyfin, mkvcheck, notify, organize, prechecks, settings, store,
                            titles, unidentified)

JobSkipped = prechecks.JobSkipped


def _safely(step, func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except (prechecks.JobSkipped, KeyboardInterrupt):
        raise
    except Exception as error:  # noqa: BLE001 - optional automation must not break the rip
        if type(error).__name__ == "RipperException":
            raise
        logging.exception(f"Automation step '{step}' failed; continuing without it: {error}")
        return None


def after_identify(job):
    """Right after identification: score it, check for an earlier rip and for disk space"""
    store.update_job(job.job_id, label=job.label)
    _safely("title spelling", confidence.prefer_tmdb_title, job)
    result = _safely("confidence", confidence.assess, job)
    _safely("already ripped", prechecks.check_already_ripped, job)
    _safely("disk space", prechecks.check_disk_space, job)
    _safely("hardware encoder check", hwcheck.precheck, job)
    return result


def wait_for_user(job, result):
    """
    Replace ARM's fixed manual wait: skip it when sure, wait longer and ask when unsure.
    :return: (seconds to wait, force) for utils.check_for_wait
    """
    if result is None or job.disctype not in ("dvd", "bluray"):
        return None, False
    if result["level"] == "high":
        logging.info(f"Confidence {result['score']}/100: not waiting for manual changes")
        return 0, False
    if result["level"] == "low":
        seconds = settings.get("LOW_CONFIDENCE_WAIT_TIME") or int(job.config.MANUAL_WAIT_TIME or 0)
        _safely("ask the user", confidence.ask_user, job, result, seconds)
        return seconds, True
    return None, False


def after_wait(job, result):
    """The manual wait is over: a low score nobody answered means the disc is filed as unidentified"""
    _safely("unidentified", unidentified.mark_if_unanswered, job, result)


def unidentified_directory(job):
    """Where to put the files instead of movies/TV (C16), or None"""
    return _safely("unidentified folder", unidentified.directory, job)


def drop_empty_folder(folder):
    _safely("remove empty folder", unidentified.remove_if_empty, folder)


def unfinalized_files(folder):
    """MakeMKV output that isn't a readable Matroska file (CHECK_UNFINALIZED)"""
    if not settings.get("CHECK_UNFINALIZED"):
        return []
    return _safely("check MakeMKV files", mkvcheck.unfinalized_files, folder) or []


def after_track_info(job, raw):
    """MakeMKV listed the titles: analyse them. Returns the plan or None."""
    decisions = _safely("title analysis", titles.plan, job, raw)
    if decisions and job.disctype == "bluray" and settings.get("CONFIDENCE_ENABLED") and not job.title_manual:
        # Blu-ray lengths are only known now; rescore and tell the user if it looks wrong
        main = titles.main_seconds(decisions)
        if main:
            result = _safely("confidence", confidence.evaluate, job, main)
            if result:
                result["level"] = confidence.level(result["score"])
                store.update_job(job.job_id, confidence=result)
                if result["level"] == "low":
                    _safely("ask the user", confidence.ask_user, job, result, 0)
                    after_wait(job, result)  # too late to wait now: nobody can answer
    return decisions


def apply_plan(job, decisions):
    """Set track.process from the plan. Returns True if anything is skipped (rip titles one by one)."""
    if not decisions:
        return False
    skipped = False
    for track in job.tracks:
        try:
            decision = decisions.get(int(track.track_number))
        except (TypeError, ValueError):
            decision = None
        if decision is None:
            continue
        track.process = decision["rip"]
        if not decision["rip"] and decision["role"] != "short":
            skipped = True
    # Save now: an uncommitted change here kept SQLite write-locked for the whole MakeMKV
    # rip (15-20 min), which blocked the web UI and every other job
    from arm.ui import db
    db.session.commit()
    return skipped


def main_track_number(job, decisions):
    main = next((d for d in (decisions or {}).values() if d["role"] == "main"), None)
    return str(main["tid"]) if main else None


def handbrake_args(job):
    """Extra HandBrake options for TRIM_TRACKS"""
    if not settings.get("TRIM_TRACKS"):
        return []
    audio = settings.languages("KEEP_AUDIO_LANGUAGES")
    subtitles = settings.languages("KEEP_SUBTITLE_LANGUAGES")
    args = []
    if audio:
        args += ["--audio-lang-list", ",".join(audio), "--all-audio"]
    if subtitles:
        args += ["--subtitle-lang-list", ",".join(subtitles), "--all-subtitles"]
    return args


def finish(job, source_dir, final_directory):
    """
    Move the finished files into place (when the titles were analysed) and check them.
    :return: True if the files were organized here (so ARM's own move step is skipped),
             and the list of length problems
    """
    decisions = {d["tid"]: d for d in (store.load_job(job.job_id).get("titles") or {}).get("titles", [])}
    if decisions and source_dir and job.disctype in ("dvd", "bluray"):
        problems = _safely("organize", organize.organize, job, source_dir, final_directory, decisions)
        if problems is not None:
            return True, problems
    return False, []


def check_main(job, final_directory):
    return _safely("check files", organize.verify_main_file, job, final_directory) or []


def after_move(job, final_directory, problems, raw_paths):
    """After the files are in place: record the encoder, report problems, tell Jellyfin"""
    _safely("encoder used", hwcheck.record_encoder, job)
    if problems:
        _safely("report", organize.report_problems, job, problems, raw_paths)
    _safely("jellyfin", jellyfin.announce, job, final_directory)
    _safely("unidentified stills", unidentified.after_move, job, final_directory)


def regular_notification(title, body):
    """ARM's own notifications, forwarded to ntfy when NTFY_SEND_ALL is on"""
    if settings.get("NTFY_SEND_ALL"):
        _safely("ntfy", notify.send, title, body, priority="low", tags=["dvd"])
