"""
Low confidence and nobody answered (LOW_CONFIDENCE_ACTION = unidentified): rip and transcode
as usual, but file the disc under

    unidentified/<disc label> (job N)/<guess> (Year).mkv

instead of under the guess, keep the guess as a suggestion and the "Not sure" banner up,
and save a few stills to help recognise it. Accepting or picking a title later moves the
folder to movies/TV and renames the files (organize.retitle).
"""
import logging
import os
import shutil
import subprocess

import arm.config.config as cfg
from arm.automation import settings, store

VIDEO = (".mkv", ".mp4", ".m4v", ".avi")


def mark_if_unanswered(job, result):
    """
    After the wait (or a Blu-ray rescore with no wait): remember that the title is only a guess.
    :return: True when the job will be filed as unidentified
    """
    if not result or result.get("level") != "low" or settings.get("LOW_CONFIDENCE_ACTION") != "unidentified":
        return False
    if job.title_manual or job.disctype not in ("dvd", "bluray"):
        return False
    data = store.load_job(job.job_id)
    if data.get("started_now") or (data.get("confidence") or {}).get("confirmed"):
        return False
    guess = {"title": job.title, "year": job.year, "video_type": job.video_type, "imdb_id": job.imdb_id}
    store.update_job(job.job_id, unidentified={"guess": guess})
    logging.info(f"Nobody confirmed '{job.title}' (low confidence): filing this disc under unidentified; "
                 "pick or accept the title later to move it")
    return True


def folder_name(job):
    from arm.automation import confidence, organize
    label = confidence.clean_label(job.label) if job.label else ""
    label = label.title() if label else "Unlabelled disc"
    return organize.safe_name(f"{label} (job {job.job_id})")


def directory(job):
    """The unidentified folder for this job, or None when it should go to movies/TV as usual"""
    if job.title_manual:
        return None  # named by the user during the rip
    data = store.load_job(job.job_id)
    if not data.get("unidentified") or (data.get("confidence") or {}).get("confirmed"):
        return None
    return os.path.join(str(cfg.arm_config["COMPLETED_PATH"]), "unidentified", folder_name(job))


def remove_if_empty(folder):
    """Drop the movies/TV folder made at the start of the rip when nothing but a poster went in"""
    if not folder or not os.path.isdir(folder):
        return
    for _root, _dirs, files in os.walk(folder):
        if any(name.lower().endswith(VIDEO) for name in files):
            return
    shutil.rmtree(folder, ignore_errors=True)


def main_file(folder):
    best = None
    for root, _dirs, files in os.walk(folder or ""):
        for name in files:
            if name.lower().endswith(VIDEO):
                path = os.path.join(root, name)
                size = os.path.getsize(path)
                if best is None or size > best[0]:
                    best = (size, path)
    return best[1] if best else None


def still_times(seconds):
    """One still near the start (title card), then five spread over the film, skipping the ends"""
    if not seconds or seconds < 60:
        return [5]
    times = [min(120, seconds * 0.1)]
    start, end = min(300, seconds * 0.1), seconds - min(300, seconds * 0.1)
    times += [start + (end - start) * i / 4 for i in range(5)]
    return [int(t) for t in times]


def stills_dir(job_id):
    path = os.path.join(store.base_dir(), "stills", str(int(job_id)))
    os.makedirs(path, exist_ok=True)
    return path


def save_stills(job, folder):
    """JPEG stills of the main file, kept with the automation records (not in the media folder)"""
    from arm.automation import organize
    path = main_file(folder)
    if not path:
        return []
    target = stills_dir(job.job_id)
    stills = []
    for index, at in enumerate(still_times(organize.duration_seconds(path))):
        out = os.path.join(target, f"still_{index}.jpg")
        try:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", path, "-frames:v", "1",
                            "-vf", "scale=640:-2", out], capture_output=True, timeout=60, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            logging.info(f"Couldn't save a still at {at} s: {error}")
            continue
        if os.path.exists(out):
            stills.append({"file": out, "at": at})
    store.update_job(job.job_id, stills=stills)
    return stills


def still_url(job_id, index):
    from arm.automation import notify
    return notify.arm_url(f"/automation/still?job={int(job_id)}&n={int(index)}"
                          f"&token={notify.sign(job_id, f'still{int(index)}')}")


def after_move(job, final_directory):
    """Save stills and ask "Do you know this one?" for a job filed as unidentified"""
    in_unidentified = "/unidentified/" in str(final_directory).replace("\\", "/")
    if not in_unidentified or not store.load_job(job.job_id).get("unidentified"):
        return
    from arm.automation import notify, ocr
    stills = save_stills(job, final_directory)
    guess = job.title + (f" ({job.year})" if job.year and job.year != "0000" else "")
    store.add_attention(job.job_id, "confidence", f"Ripped as unidentified: nobody confirmed {guess}. "
                                                  "Accept it or pick the title to move the files.")
    message = (f"Nobody confirmed the title, so the disc (label {job.label or 'none'}) went to "
               f"{final_directory}. A2RM's guess: {guess}. Accept it or pick the title and the files move."
               + ocr.hint(job.job_id))
    actions = [notify.http(f"It's {guess}"[:40], notify.action_url(job.job_id, "confirm")),
               notify.view("Pick the title", notify.arm_url(f"/jobdetail?job_id={job.job_id}#identify"))]
    notify.in_app("Do you know this one?", message)
    notify.send("Do you know this one?", message, priority="default", tags=["dvd", "question"],
                click=notify.job_link(job.job_id), actions=actions,
                attach=still_url(job.job_id, 0) if stills else None)
