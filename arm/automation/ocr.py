"""
Title-card OCR (TITLE_OCR, off by default): when ARM isn't sure what a film is, read the text on
stills from the first minutes with Tesseract (on this server) and look the readable phrases up on
TMDb, keeping results whose name matches and whose runtime fits the main title. The best one
becomes the job's suggestion ("Use suggestion" / the "Not sure" banner).

Nothing but the phrases themselves leaves the network (as TMDb searches, as now). Works when the
title is set in plain type; stylised logos usually can't be read. Needs `tesseract` in the image.
"""
import collections
import logging
import os
import re
import shutil
import subprocess
import tempfile

from arm.automation import settings, store

FIRST_SECONDS = 300
STEP_SECONDS = 20
MIN_SIMILARITY = 0.8
STOP_WORDS = ("presents", "production", "pictures", "films", "a film by", "directed", "starring", "music by",
              "produced", "screenplay", "copyright", "rated", "warning", "fbi", "interpol", "all rights",
              "entertainment", "studios", "distributed", "casting", "executive", "edited", "written by")
LINE = re.compile(r"[^A-Za-z0-9 '&:!?,.-]")


def available():
    return bool(shutil.which("tesseract"))


def frame_times(seconds):
    end = min(FIRST_SECONDS, int(seconds * 0.5)) if seconds else FIRST_SECONDS
    return list(range(STEP_SECONDS // 2, max(end, STEP_SECONDS) + 1, STEP_SECONDS))


def read_text(path, seconds):
    """Text Tesseract finds on frames from the first minutes, one string per frame"""
    texts = []
    with tempfile.TemporaryDirectory(prefix="a2rm-ocr-") as tmp:
        for at in frame_times(seconds):
            image = os.path.join(tmp, f"{at}.png")
            try:
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", path, "-frames:v", "1",
                                "-vf", "scale=1280:-2,format=gray", image], capture_output=True, timeout=60,
                               check=True)
                result = subprocess.run(["tesseract", image, "-", "--psm", "11"], capture_output=True, text=True,
                                        timeout=60)
            except (OSError, subprocess.SubprocessError) as error:
                logging.info(f"Title OCR: frame at {at} s failed: {error}")
                continue
            if result.stdout.strip():
                texts.append(result.stdout)
    return texts


def phrases(texts, limit=8):
    """Lines that could be a title: mostly letters, 1-6 words, not credits boilerplate; most frequent first"""
    counts = collections.Counter()
    for text in texts:
        for line in text.splitlines():
            line = " ".join(LINE.sub(" ", line).split()).strip(" -.,:")
            letters = sum(ch.isalpha() for ch in line)
            if len(line) < 3 or len(line) > 40 or letters < 0.7 * len(line.replace(" ", "")):
                continue
            words = line.split()
            if not 1 <= len(words) <= 6 or (len(words) == 1 and len(line) < 4):
                continue
            if any(stop in line.lower() for stop in STOP_WORDS):
                continue
            counts[line.title() if line.isupper() else line] += 1
    return [phrase for phrase, _count in sorted(counts.items(), key=lambda pc: (-pc[1], -len(pc[0])))[:limit]]


def candidates(found, main_seconds, series=False):
    """TMDb results whose name matches a phrase (and, for movies, whose runtime fits)"""
    from arm.automation import confidence, tmdb
    results = {}
    minutes = main_seconds / 60 if main_seconds else None
    for rank, phrase in enumerate(found):
        options = [dict(c, video_type="series") for c in tmdb.search_tv(phrase, limit=3)] if series else \
            [dict(c, video_type="movie") for c in tmdb.search_movies(phrase, limit=5)]
        for option in options:
            similar = confidence.similarity(phrase, option["title"])
            if similar < MIN_SIMILARITY:
                continue
            runtime = option.get("runtime")
            if not series and minutes and runtime and abs(runtime - minutes) > max(5, 0.1 * runtime):
                continue
            key = option.get("imdb_id") or option["title"]
            score = similar + (0.2 if runtime and minutes else 0) - 0.02 * rank
            if key not in results or results[key]["score"] < score:
                results[key] = {"title": option["title"], "year": option.get("year"),
                                "imdb_id": option.get("imdb_id"), "poster_url": option.get("poster_url"),
                                "video_type": option["video_type"], "phrase": phrase, "score": round(score, 2)}
    return sorted(results.values(), key=lambda c: -c["score"])[:3]


def needed(job):
    """Only for jobs ARM isn't sure about that nobody has named"""
    data = store.load_job(job.job_id)
    result = data.get("confidence") or {}
    return (not job.title_manual and job.disctype in ("dvd", "bluray")
            and (result.get("level") == "low" or bool(data.get("unidentified"))) and not result.get("confirmed"))


def after_move(job, folder):
    """Read the title card of the main file and suggest a title. Returns the candidates."""
    if not settings.get("TITLE_OCR") or not needed(job):
        return []
    if not available():
        logging.info("Title OCR is on but tesseract isn't installed in this image")
        return []
    from arm.automation import organize, unidentified
    path = unidentified.main_file(folder)
    if not path:
        return []
    seconds = organize.duration_seconds(path)
    found = phrases(read_text(path, seconds))
    logging.info(f"Title OCR read: {found}")
    best = candidates(found, seconds, series=job.video_type == "series") if found else []
    store.update_job(job.job_id, ocr={"phrases": found, "candidates": best})
    if best:
        top = best[0]
        data = store.load_job(job.job_id)
        result = dict(data.get("confidence") or {})
        if not result.get("suggestion"):
            result["suggestion"] = {key: top.get(key) for key in ("title", "year", "imdb_id", "poster_url",
                                                                  "video_type")}
            store.update_job(job.job_id, confidence=result)
        names = ", ".join(f"{c['title']} ({c['year']})" if c.get("year") else c["title"] for c in best)
        store.add_attention(job.job_id, "ocr", f"The title card reads \"{top['phrase']}\": maybe {names}.")
    return best


def hint(job_id):
    """One line for notifications, or ''"""
    best = (store.load_job(job_id).get("ocr") or {}).get("candidates") or []
    if not best:
        return ""
    top = best[0]
    return f" The title card reads \"{top['phrase']}\": maybe {top['title']}" + \
        (f" ({top['year']})." if top.get("year") else ".")
