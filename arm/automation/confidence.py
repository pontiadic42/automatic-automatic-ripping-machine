"""
How sure is ARM about a disc's title? A 0-100 score with plain-language reasons, from:
the disc label vs the title, the main title's runtime vs TMDb, and earlier rips of the
same disc. When the label is vague, TMDb results are compared by runtime to find a
better match.
"""
import difflib
import logging
import re

from arm.automation import settings, store, tmdb

GENERIC_LABELS = {"", "none", "not identified", "dvd", "dvd video", "dvdvideo", "video ts", "bdrom", "bd rom",
                  "disc", "disc 1", "dvd volume", "dvdvolume", "logical volume id", "movie", "untitled",
                  "blu ray", "bluray", "no label"}
NOISE_WORDS = {"dvd", "bd", "bluray", "ws", "fs", "widescreen", "fullscreen", "ntsc", "pal", "16x9", "4x3",
               "r1", "r2", "r4", "us", "uk", "eu", "disc", "disk", "d1", "d2", "side", "a", "b"}
DISC_MARKERS = re.compile(r"\b(d|disc|disk|side)\s*\d+\b", re.IGNORECASE)
# Box-set words: DR_WHO_COMPLETE_SERIES_2018_D23, FRIENDS_SEASON_3_DISC_2, OFFICE_S02_D1
TV_MARKERS = re.compile(r"\b(the\s+)?complete(\s+(series|season|seasons|collection))?\b|\b(season|series|vol|volume)"
                        r"\s*\d+\b|\bs\d{1,2}(\s*d\d{1,2})?\b|\b(19|20)\d{2}\b|\b(box\s*set|collection)\b",
                        re.IGNORECASE)
TV_HINT = re.compile(r"\bcomplete\s+(series|season|seasons)\b|\b(season|series)\s*\d+\b|\bs\d{1,2}\s*d\d{1,2}\b|"
                     r"\bs\d{1,2}\b|\btv\s+series\b|\bepisodes?\b", re.IGNORECASE)
ABBREVIATIONS = {"dr": "doctor", "mr": "mister", "mrs": "missus", "st": "saint"}


DISCDB_SCORE = 98


def clean_label(label):
    """DONT_BREATHE_WS_D1 -> 'dont breathe'; DR_WHO_COMPLETE_SERIES_2018_D23 -> 'dr who'"""
    text = str(label or "").replace("_", " ").replace(".", " ").strip().lower()
    if text in GENERIC_LABELS:
        return ""
    text = TV_MARKERS.sub(" ", DISC_MARKERS.sub(" ", text))
    words = [word for word in re.split(r"[^a-z0-9']+", text) if word and word not in NOISE_WORDS]
    return " ".join(words)


def looks_like_tv(label):
    """The label says it's a TV box set or season disc"""
    return bool(TV_HINT.search(str(label or "").replace("_", " ").replace(".", " ")))


def normalize_title(title):
    text = re.sub(r"[^a-z0-9 ]+", " ", str(title or "").lower().replace("'", "").replace("&", " and "))
    words = [ABBREVIATIONS.get(word, word) for word in re.sub(r"\s+", " ", text).strip().split(" ")]
    return re.sub(r"^(the|a|an) ", "", " ".join(words))


def similarity(label, title):
    """0..1 how well the cleaned label matches the title"""
    left, right = normalize_title(clean_label(label)), normalize_title(title)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    # Labels are often abbreviated: "it chapter two" vs "it chapter 2", "lotr fotr"
    left_words, right_words = set(left.split()), set(right.split())
    overlap = len(left_words & right_words) / max(len(left_words), 1)
    return round(max(ratio, overlap * 0.9), 3)


def score(identified, label_usable, label_sim, runtime_diff, prior, series=False):
    """
    :param identified: ARM found a title (hasnicetitle)
    :param label_usable: the label had real words in it
    :param label_sim: 0..1 label vs title
    :param runtime_diff: minutes between the main title and TMDb's runtime, or None
                         (TV shows: the title closest to TMDb's episode runtime)
    :param prior: "same" (earlier rip of this disc had this title), "different", or None
    :param series: it's a TV show (the reasons talk about episodes)
    :return: (score, reasons)
    """
    what = "An episode-length title" if series else "The main title"
    reasons = []
    if not identified:
        return 5, ["ARM couldn't find a title for this disc"]
    points = 30
    if not label_usable:
        reasons.append("The disc label doesn't say what it is")
    elif label_sim >= 0.85:
        points += 30
        reasons.append("The disc label matches the title")
    elif label_sim >= 0.6:
        points += 18
        reasons.append("The disc label mostly matches the title")
    elif label_sim >= 0.4:
        points += 8
        reasons.append("The disc label only partly matches the title")
    else:
        points -= 10
        reasons.append("The disc label doesn't match the title")

    if runtime_diff is None:
        reasons.append("No runtime to compare")
    elif runtime_diff <= 3:
        points += 35
        reasons.append(f"{what}'s length matches TMDb's " + ("episode runtime" if series else "runtime"))
    elif runtime_diff <= 8:
        points += 15
        reasons.append(f"{what} is {runtime_diff:.0f} min off TMDb's runtime")
    elif runtime_diff <= 20:
        points -= 10
        reasons.append(f"{what} is {runtime_diff:.0f} min off TMDb's runtime"
                       + ("" if series else " (maybe another cut)"))
    else:
        points -= 30
        reasons.append(f"{what} is {runtime_diff:.0f} min off TMDb's runtime")

    if prior == "same":
        points += 25
        reasons.append("An earlier rip of this disc had the same title")
    elif prior == "different":
        points -= 15
        reasons.append("An earlier rip of this disc had a different title")
    return max(0, min(100, int(points))), reasons


def level(value):
    if value >= settings.get("CONFIDENCE_SKIP_WAIT_ABOVE") and settings.get("CONFIDENCE_SKIP_WAIT_ABOVE") > 0:
        return "high"
    if value < settings.get("CONFIDENCE_NOTIFY_BELOW"):
        return "low"
    return "medium"


def title_lengths(job):
    """Lengths (seconds) of the disc's titles before ripping (DVD via lsdvd; Blu-ray only later, from MakeMKV)"""
    if job.disctype != "dvd":
        return []
    from arm.ripper import identify
    tracks = identify.lsdvd_tracks(job.devpath) or []
    return [float(track.get("length") or 0) for track in tracks]


def main_title_seconds(job):
    """Length of the longest title, before ripping"""
    lengths = title_lengths(job)
    return max(lengths) if lengths else None


def prior_rip(job):
    """'same' / 'different' / None, from successful jobs with the same disc fingerprint"""
    if not job.crc_id:
        return None
    from arm.models.job import Job
    earlier = Job.query.filter(Job.crc_id == job.crc_id, Job.status == "success", Job.job_id != job.job_id).all()
    if not earlier:
        return None
    titles = {normalize_title(other.title_manual or other.title) for other in earlier}
    return "same" if normalize_title(job.title) in titles else "different"


def earlier_identity(job):
    """The newest successful, identified rip of the same disc (same DVD fingerprint), or None"""
    if not job.crc_id:
        return None
    from arm.models.job import Job
    earlier = Job.query.filter(Job.crc_id == job.crc_id, Job.status == "success", Job.job_id != job.job_id,
                               Job.hasnicetitle.is_(True)).order_by(Job.job_id.desc()).all()
    for other in earlier:
        title = other.title_manual or other.title
        if normalize_title(title) not in ("", "none", "not identified") and other.video_type != "unknown":
            return other
    return None


def prefer_tmdb_title(job):
    """
    Use TMDb's spelling of the identified title. The disc-ID database and the label give
    "World-War-Z" (words joined by hyphens); TMDb has "World War Z". Only the spelling
    changes: the title is replaced only when it has the same words as TMDb's.
    :return: the new title, or None if nothing changed
    """
    from arm.ripper import utils
    if job.title_manual or not job.imdb_id or not job.title:
        return None
    tmdb_title = tmdb.title_for_imdb(job.imdb_id)
    if not tmdb_title:
        return None
    clean = utils.clean_for_filename(tmdb_title)
    if clean == job.title or normalize_title(clean) != normalize_title(job.title):
        return None
    logging.info(f"Using TMDb's spelling of the title: '{job.title}' -> '{clean}'")
    utils.database_updater({"title": clean, "title_auto": clean}, job)
    return clean


def adopt(job, title, year, imdb_id, poster_url, video_type):
    from arm.ripper import utils
    utils.database_updater({
        "title": title, "title_auto": title, "year": year, "year_auto": year, "imdb_id": imdb_id,
        "imdb_id_auto": imdb_id, "poster_url": poster_url, "poster_url_auto": poster_url,
        "video_type": video_type, "video_type_auto": video_type, "hasnicetitle": True,
    }, job)


def evaluate(job, main_seconds, lengths=None):
    """
    Score the job's current title. TV shows are compared by episode: the disc title closest to
    TMDb's episode runtime (the longest title on a TV disc is usually a "play all").
    """
    runtime = tmdb.runtime_minutes(job.imdb_id) if (main_seconds or lengths) else None
    series = job.video_type == "series"
    diff = None
    if runtime and series and lengths:
        diff = min(abs(seconds / 60 - runtime) for seconds in lengths if seconds)
    elif runtime and main_seconds:
        diff = abs(main_seconds / 60 - runtime)
    label = clean_label(job.label)
    value, reasons = score(bool(job.hasnicetitle) and normalize_title(job.title) not in ("", "none", "not identified"),
                           bool(label), similarity(job.label, job.title), diff, prior_rip(job), series=series)
    from arm.automation import discdb
    hit = discdb.hit_for(job)
    if hit:
        value = max(value, DISCDB_SCORE)
        reasons.insert(0, f"TheDiscDB knows this disc ({hit['release'] or hit['title']})")
    return {"score": value, "reasons": reasons, "main_minutes": round(main_seconds / 60, 1) if main_seconds else None,
            "tmdb_runtime": runtime}


def runtime_candidate(job, main_seconds):
    """A TMDb result whose runtime matches the main title, searched by the label"""
    query = clean_label(job.label)
    if not query or not main_seconds:
        return None
    minutes = main_seconds / 60
    matches = [c for c in tmdb.search_movies(query) if c["runtime"] and abs(c["runtime"] - minutes) <= 3]
    if not matches:
        return None
    matches.sort(key=lambda c: (-similarity(job.label, c["title"]), abs(c["runtime"] - minutes), -c["popularity"]))
    return matches[0]


def tv_candidate(job):
    """For a label that says it's a TV box set or season: the TV show whose name matches it"""
    query = clean_label(job.label)
    if not query or not looks_like_tv(job.label):
        return None
    shows = [show for show in tmdb.search_tv(query) if similarity(job.label, show["title"]) >= 0.85]
    shows.sort(key=lambda show: (-similarity(job.label, show["title"]), -show["popularity"]))
    return shows[0] if shows else None


def best_alternative(job):
    """
    A likely title to offer the user when the score stays low: TV shows first for TV-looking
    labels, otherwise the best label match among movies and TV shows.
    """
    query = clean_label(job.label)
    if not query:
        return None
    candidates = tmdb.search_tv(query) if looks_like_tv(job.label) else []
    if not candidates:
        candidates = [dict(movie, video_type="movie") for movie in tmdb.search_movies(query, limit=5)]
        candidates += tmdb.search_tv(query, limit=3)
    candidates = [c for c in candidates if similarity(job.label, c["title"]) >= 0.6
                  and normalize_title(c["title"]) != normalize_title(job.title)]
    if not candidates:
        return None
    best = max(candidates, key=lambda c: (similarity(job.label, c["title"]), c["popularity"]))
    return {key: best.get(key) for key in ("title", "year", "imdb_id", "poster_url", "video_type")}


def _try(job, result, candidate, video_type, reason, main_seconds, lengths):
    """Adopt a candidate title; keep it only if it scores better"""
    before = (job.title, job.year, job.imdb_id, job.poster_url, job.video_type)
    adopt(job, candidate["title"], candidate["year"], candidate["imdb_id"], candidate["poster_url"], video_type)
    retry = evaluate(job, main_seconds, lengths)
    if retry["score"] <= result["score"]:
        adopt(job, *before)
        return result
    retry["reasons"].insert(0, reason)
    return retry


def _improve(job, result, main_seconds, lengths):
    """A weak identification: try an earlier rip of the same disc, a TV show match, a runtime match"""
    low = settings.get("CONFIDENCE_NOTIFY_BELOW")
    if result["score"] < low:
        # The same disc was ripped and identified before: use that title
        other = earlier_identity(job)
        if other is not None and normalize_title(other.title_manual or other.title) != normalize_title(job.title):
            title = other.title_manual or other.title
            logging.info(f"Same disc as job {other.job_id}, which was ripped as '{title}'; using that title")
            adopt(job, title, other.year, other.imdb_id, other.poster_url, other.video_type)
            retry = evaluate(job, main_seconds, lengths)
            retry["reasons"].insert(0, f"Same disc as job {other.job_id}, which was ripped as {title}")
            result = retry
    if result["score"] < low:
        show = tv_candidate(job)
        if show and (normalize_title(show["title"]) != normalize_title(job.title) or job.video_type != "series"):
            logging.info(f"The label looks like a TV set: trying the TV show '{show['title']} ({show['year']})'")
            result = _try(job, result, show, "series", f"The label looks like a TV show: {show['title']}",
                          main_seconds, lengths)
    if result["score"] < low and settings.get("RUNTIME_MATCHING") and not looks_like_tv(job.label):
        candidate = runtime_candidate(job, main_seconds)
        if candidate and normalize_title(candidate["title"]) != normalize_title(job.title):
            previous = job.title
            logging.info(f"Runtime match: '{candidate['title']} ({candidate['year']})' has a "
                         f"{candidate['runtime']} min runtime like the main title; was '{job.title}'")
            adopt(job, candidate["title"], candidate["year"], candidate["imdb_id"], candidate["poster_url"], "movie")
            retry = evaluate(job, main_seconds, lengths)
            retry["reasons"].insert(0, f"Picked by runtime instead of '{previous}'")
            result = retry
    return result


def assess(job):
    """
    Score the identification, try better matches when it's weak, save the result and
    return it. Values the user set in the UI are never replaced.
    """
    if not settings.get("CONFIDENCE_ENABLED") or job.disctype not in ("dvd", "bluray"):
        return None
    if job.title_manual:
        result = {"score": 100, "reasons": ["You set the title"], "level": "high"}
        store.update_job(job.job_id, confidence=result)
        return result
    try:
        lengths = title_lengths(job)
    except Exception as error:  # noqa: BLE001
        logging.info(f"Couldn't read the title lengths: {error}")
        lengths = []
    main_seconds = max(lengths) if lengths else None
    result = _improve(job, evaluate(job, main_seconds, lengths), main_seconds, lengths)
    result["level"] = level(result["score"])
    if result["level"] == "low":
        suggestion = best_alternative(job)
        if suggestion:
            result["suggestion"] = suggestion
    store.update_job(job.job_id, confidence=result)
    logging.info(f"Identification confidence {result['score']}/100 ({result['level']}): "
                 + "; ".join(result["reasons"])
                 + (f". Suggesting {result['suggestion']['title']}" if result.get("suggestion") else ""))
    return result


def suggestion_name(suggestion):
    """Doctor Who (TV show, 2005)"""
    parts = ("TV show" if suggestion.get("video_type") == "series" else "", suggestion.get("year") or "")
    details = ", ".join(part for part in parts if part)
    return suggestion["title"] + (f" ({details})" if details else "")


def ask_user(job, result, wait_seconds):
    """Low confidence: send the phone notification with Confirm / Pick another"""
    from arm.automation import notify
    minutes = max(1, round(wait_seconds / 60))
    pick = notify.view("Pick the title", notify.arm_url(f"/titlesearch?job_id={job.job_id}"))
    if normalize_title(job.title) in ("", "none", "not identified") or not job.hasnicetitle:
        store.add_attention(job.job_id, "confidence", "ARM couldn't identify this disc. Pick the title.")
        title = "Which disc is this?"
        body = (f"ARM couldn't identify the disc in {job.devpath} (label: {job.label or 'none'}). "
                f"It rips it as unidentified in {minutes} min unless you pick the title.")
        actions = [pick]
    else:
        store.add_attention(job.job_id, "confidence",
                            f"Not sure this is {job.title} ({result['score']}/100). Confirm or pick another title.")
        title = f"Is this {job.title}" + (f" ({job.year})" if job.year and job.year != "0000" else "") + "?"
        body = (f"{result['score']}/100 sure. " + "; ".join(result["reasons"][:3]) +
                (f". In {minutes} min ARM rips it into the unidentified folder (you can still name it later)."
                 if settings.get("LOW_CONFIDENCE_ACTION") == "unidentified" and wait_seconds
                 else f". ARM rips with this title in {minutes} min unless you change it."))
        actions = [notify.http("Yes, that's it", notify.action_url(job.job_id, "confirm")), pick]
    suggestion = result.get("suggestion")
    if suggestion:
        name = suggestion_name(suggestion)
        body += f" Maybe it's {name}?"
        actions.insert(len(actions) - 1, notify.http(f"Use {name}"[:40], notify.action_url(job.job_id, "suggest")))
    if wait_seconds and len(actions) < 3 and settings.get("START_NOW_BUTTONS"):
        # ntfy shows at most 3 buttons
        actions.insert(len(actions) - 1, notify.http("Start now", notify.action_url(job.job_id, "start")))
    notify.in_app(title, body)
    notify.send(title, body, priority="high", tags=["dvd", "question"], click=notify.job_link(job.job_id),
                icon=job.poster_url, actions=actions)
