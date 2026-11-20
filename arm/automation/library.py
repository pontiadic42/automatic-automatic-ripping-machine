"""
Library manager (C35): look after everything in the movies, tv and unidentified folders under
COMPLETED_PATH, including files A2RM didn't make or doesn't track.

- scan(): one entry per top-level folder (or loose video file) with what looks wrong: not
  tracked by any job, ARM's "_<timestamp>" copy folders, loose files, MakeMKV names (B1_t00.mkv)
  that were never renamed, missing/odd years, and folders for the same title (duplicates, or
  one show split over many folders). Only names and sizes are read: it's quick.
- plan(): what renaming/moving an item would do (dry run): to movies or TV, under a new title,
  merging into a folder that's already there, episodes numbered from a season and first
  episode. Nothing is ever overwritten: a file that's already there with the same size is left
  where it was, a different one gets " (2)".
- apply(): does a plan, updates the jobs that owned the folder, writes the .nfo, tells Jellyfin.
- trash(): moves an item to COMPLETED_PATH/.a2rm-trash/<time>/ (nothing is deleted).
- Every change is in the log on the page and can be undone.
"""
import datetime
import logging
import os
import re
import shutil
from types import SimpleNamespace

import arm.config.config as cfg
from arm.automation import store

ROOTS = ("movies", "tv", "unidentified")
KIND_ROOT = {"movie": "movies", "series": "tv"}
VIDEO = (".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts")
TRASH = ".a2rm-trash"
LOG_SIZE = 50
COPY_SUFFIX = re.compile(r"_(\d{9,})$")
YEAR = re.compile(r"\s*\((\d{4})(?:\s*[-–]\s*(?:\d{4})?)?\)\s*$")
NONE_YEAR = re.compile(r"\s*\((?:None|0000|)\)\s*$", re.IGNORECASE)
SEASON_HINT = re.compile(r"\s+(?:-\s+)?Season\s*(\d{1,2})(?:\s*Ep(?:isodes?)?\.?\s*(\d{1,3})(?:\s*-\s*\d{1,3})?)?.*$",
                         re.IGNORECASE)
RAW_NAME = re.compile(r"(?:^|_)t\d{2,3}\.[A-Za-z0-9]+$")
TID = re.compile(r"_t(\d{2,3})\.[A-Za-z0-9]+$")
EPISODE_NAME = re.compile(r"S(\d{1,2})E(\d{1,3})", re.IGNORECASE)
ROLES = ("main", "version", "episode", "extra", "keep")
# Labels offered for a movie file (C40). A label fills {Edition}: "Fast Five (2011) - Extended cut.mkv", the form
# Jellyfin shows as versions of one movie.
EDITIONS = ("Theatrical", "Extended cut", "Director's cut", "Unrated", "Special edition", "Remastered", "IMAX")
EDITION_MAX = 40
INFO_ONLY = {"untracked"}  # worth a filter, but a well-named folder A2RM didn't make is fine
SIDE_FILES = (".nfo", ".srt", ".ass", ".sub", ".idx", ".jpg", ".png")


class LibraryError(ValueError):
    """Something the user can fix (shown on the page)"""


# ---------------------------------------------------------------- paths

def completed():
    return os.path.normpath(str(cfg.arm_config["COMPLETED_PATH"]))


def absolute(rel):
    """Path under COMPLETED_PATH from a page value; refuses anything outside it"""
    rel = str(rel or "").replace("\\", "/").strip("/")
    if not rel or any(part in ("..", "") for part in rel.split("/")):
        raise LibraryError("That isn't a library path.")
    path = os.path.normpath(os.path.join(completed(), rel))
    if not path.startswith(completed() + os.sep):
        raise LibraryError("That isn't a library path.")
    return path


def relative(path):
    return os.path.relpath(path, completed()).replace("\\", "/")


def item_of(path):
    """'movies/X (2001)' for any path inside that item, or None"""
    try:
        parts = relative(os.path.normpath(path)).split("/")
    except ValueError:
        return None
    if len(parts) < 2 or parts[0] not in ROOTS:
        return None
    return "/".join(parts[:2])


def is_video(name):
    return name.lower().endswith(VIDEO) and not name.startswith(".")


# ---------------------------------------------------------------- names

def parse_name(name):
    """
    Title, year and season hints from a folder or file name:
    'Doctor Who Season 8 Ep. 5-9 (2005–)_179152795038' -> ('Doctor Who', '2005', 8, 5)
    """
    stem = name
    if is_video(stem):
        stem = os.path.splitext(stem)[0]
        edition = re.match(r"^(.*\(\d{4}[^)]*\))\s+-\s+.*$", stem)
        stem = edition.group(1) if edition else stem
    copy = COPY_SUFFIX.search(stem)
    if copy:
        stem = stem[:copy.start()]
    year = ""
    match = YEAR.search(stem)
    if match:
        year, stem = match.group(1), stem[:match.start()]
    else:
        stem = NONE_YEAR.sub("", stem)
    season = first = None
    hint = SEASON_HINT.search(stem)
    if hint:
        season = int(hint.group(1))
        first = int(hint.group(2)) if hint.group(2) else None
        stem = stem[:hint.start()]
    title = re.sub(r"\s+", " ", stem.replace("_", " ")).strip(" -.")
    return {"title": title, "year": year, "season": season, "first_episode": first, "copy": bool(copy)}


def title_key(title, year=""):
    """Comparable form: 'Annabelle- Creation' and 'Annabelle-Creation' and 'annabelle creation' match"""
    return re.sub(r"[^a-z0-9]", "", str(title or "").lower().replace("&", "and")) + (f"|{year}" if year else "")


def folder_name(title, year):
    from arm.automation.organize import safe_name
    year = str(year or "").strip()
    return safe_name(f"{title} ({year})" if year and year != "0000" else title)


def _fake_job(title, year, kind, imdb_id=""):
    return SimpleNamespace(job_id=None, title=title, title_manual=title, year=str(year or ""), video_type=kind,
                           imdb_id=imdb_id or "", label="")


def nfo_ids(folder):
    """IMDb id from an .nfo in the folder (ours, Jellyfin's or tinyMediaManager's), or ''"""
    if not os.path.isdir(folder):
        return ""
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".nfo"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as handle:
                found = re.search(r"\b(tt\d{6,9})\b", handle.read(20000))
        except OSError:
            continue
        if found:
            return found.group(1)
    return ""


# ---------------------------------------------------------------- scan

def tracked_jobs():
    """{item id: job id} for finished jobs whose folder (or files) are in the library"""
    owners = {}
    try:
        from arm.models.job import Job
        for job_id, path in Job.query.with_entities(Job.job_id, Job.path).filter(Job.path.isnot(None)).all():
            item = item_of(path) if path else None
            if item:
                owners[item] = job_id
    except Exception as error:  # noqa: BLE001 - the page still works from the records
        logging.info(f"Library: couldn't read job folders from the database: {error}")
    folder = os.path.join(store.base_dir(), "jobs")
    for name in os.listdir(folder):
        if not name.endswith(".json") or not name[:-5].isdigit():
            continue
        for entry in store.load_job(int(name[:-5])).get("files") or []:
            item = item_of(entry.get("file") or "")
            if item:
                owners.setdefault(item, int(name[:-5]))
    return owners


def _contents(path):
    """(video files relative to the item, total bytes, raw MakeMKV names outside extras)"""
    if os.path.isfile(path):
        return [os.path.basename(path)], os.path.getsize(path), int(bool(RAW_NAME.search(path)))
    videos, total, raw = [], 0, 0
    extras = str(cfg.arm_config.get("EXTRAS_SUB") or "extras").lower()
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if not is_video(name):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, path).replace("\\", "/")
            videos.append(rel)
            try:
                total += os.path.getsize(full)
            except OSError:
                pass
            if RAW_NAME.search(name) and rel.split("/")[0].lower() != extras:
                raw += 1
    return sorted(videos), total, raw


def scan(owners=None):
    """Every item in movies/, tv/ and unidentified/ with its problems; saved for the page"""
    owners = tracked_jobs() if owners is None else owners
    items = []
    for root in ROOTS:
        base = os.path.join(completed(), root)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base), key=str.lower):
            path = os.path.join(base, name)
            if name.startswith(".") or (os.path.isfile(path) and not is_video(name)):
                continue
            items.append(_describe(root, name, path, owners))
    _mark_duplicates(items)
    for item in items:
        item["attention"] = any(issue["code"] not in INFO_ONLY for issue in item["issues"])
    result = {"scanned": datetime.datetime.now().isoformat(timespec="seconds"), "items": items,
              "problems": sum(1 for item in items if item["attention"])}
    store.update_state(library=result)
    return result


def _describe(root, name, path, owners):
    parsed = parse_name(name)
    videos, total, raw = _contents(path)
    rel = f"{root}/{name}"
    item = {"id": rel, "root": root, "name": name, "loose": os.path.isfile(path), "title": parsed["title"],
            "year": parsed["year"], "season": parsed["season"], "first_episode": parsed["first_episode"],
            "videos": len(videos), "bytes": total, "job_id": owners.get(rel), "issues": []}
    issues = item["issues"]
    if item["job_id"] is None:
        issues.append({"code": "untracked", "text": "Not from an A2RM job (or no job tracks it)"})
    if root == "unidentified":
        issues.append({"code": "unidentified", "text": "Unidentified: name it to move it to movies or TV"})
    if item["loose"]:
        issues.append({"code": "loose", "text": f"Loose file in {root}/, not in its own folder"})
    if parsed["copy"]:
        issues.append({"code": "copy", "text": "ARM copy folder (name ends in _<number>): merge it"})
    if parsed["season"] is not None and root == "tv":
        issues.append({"code": "season_folder", "text": f"Folder for season {parsed['season']} only: "
                                                        "move it into the show's folder"})
    if not videos:
        issues.append({"code": "empty", "text": "No video files"})
    if raw:
        issues.append({"code": "raw_names", "text": f"{raw} file{'s' if raw != 1 else ''} still named by MakeMKV"})
    if root != "unidentified" and not parsed["year"]:
        issues.append({"code": "year", "text": "No year in the name"})
    elif root != "unidentified" and not parsed["copy"] and parsed["season"] is None and name != (
            folder_name(parsed["title"], parsed["year"]) + (os.path.splitext(name)[1] if item["loose"] else "")):
        issues.append({"code": "name", "text": f"Name isn't \"{folder_name(parsed['title'], parsed['year'])}\""})
    return item


def _mark_duplicates(items):
    groups = {}
    for item in items:
        if item["root"] in ("movies", "tv") and item["title"]:
            # a show's folders often have no or different years; films of the same name (remakes) don't
            year = item["year"] if item["root"] == "movies" else ""
            groups.setdefault((item["root"], title_key(item["title"], year)), []).append(item)
    for (root, _key), group in groups.items():
        if len(group) < 2:
            continue
        for item in group:
            others = [other["name"] for other in group if other is not item]
            listed = ", ".join(others[:3]) + (f" and {len(others) - 3} more" if len(others) > 3 else "")
            text = (f"Same show in {len(group)} folders ({listed})" if root == "tv"
                    else f"Same title as {listed}")
            item["issues"].append({"code": "duplicate", "text": text})
            item["group"] = title_key(item["title"], item["year"] if root == "movies" else "")


def same_title(item, items):
    """Other items with this title (any folder; years must agree when both have one)"""
    key = title_key(item["title"])
    return [other for other in items if other["id"] != item["id"] and key and title_key(other["title"]) == key
            and (not item["year"] or not other["year"] or item["year"] == other["year"])]


def find(item_id):
    path = absolute(item_id)
    if item_of(path) != item_id.strip("/") or not os.path.exists(path):
        raise LibraryError(f"{item_id} isn't in the library any more. Rescan.")
    root, name = item_id.strip("/").split("/", 1)
    return _describe(root, name, path, tracked_jobs())


def defaults(item):
    """Starting values for the edit form: the owning job's, else the .nfo's and the folder name's"""
    values = {"kind": "series" if item["root"] == "tv" else "movie", "title": item["title"], "year": item["year"],
              "imdb_id": "", "season": item.get("season"), "first_episode": item.get("first_episode")}
    if item.get("job_id"):
        try:
            from arm.models.job import Job
            job = Job.query.get(item["job_id"])
        except Exception:  # noqa: BLE001
            job = None
        if job is not None:
            values.update(kind=job.video_type if job.video_type in KIND_ROOT else values["kind"],
                          title=job.title_manual or job.title or values["title"],
                          year=(job.year if job.year and job.year != "0000" else values["year"]),
                          imdb_id=job.imdb_id or "")
            choice = store.load_job(job.job_id).get("tv_season") or {}
            if choice.get("season") is not None:
                values.update(season=choice["season"], first_episode=choice.get("first_episode"))
    path = absolute(item["id"])
    values["imdb_id"] = values["imdb_id"] or nfo_ids(path if os.path.isdir(path) else os.path.dirname(path))
    return values


# ---------------------------------------------------------------- plan

def default_roles(item_id, kind):
    """
    {file: role} for the edit form. Movies: the biggest top-level file is "main", other top-level
    files "version", files in sub-folders "keep". TV: top-level and season-folder files are
    "episode" except one that's much bigger than the others (a play-all), which is "extra".
    """
    path = absolute(item_id)
    base = path if os.path.isdir(path) else os.path.dirname(path)
    videos = _contents(path)[0]
    sizes = {rel: _size(os.path.join(base, rel)) for rel in videos}
    extras = str(cfg.arm_config.get("EXTRAS_SUB") or "extras").lower()
    known = _job_roles(item_id, base)
    if known and set(known) >= set(videos):
        return {rel: _role_for(known[rel], kind) for rel in videos}
    roles = {}
    if kind == "movie":
        top = [rel for rel in videos if "/" not in rel]
        main = max(top, key=lambda rel: sizes[rel]) if top else None
        for rel in videos:
            roles[rel] = "main" if rel == main else ("version" if "/" not in rel else "keep")
        return roles
    candidates = [rel for rel in videos if rel.split("/")[0].lower() != extras]
    ordered = sorted(sizes[rel] for rel in candidates)
    median = ordered[len(ordered) // 2] if ordered else 0
    for rel in videos:
        if rel not in candidates:
            roles[rel] = "keep"
        elif len(candidates) >= 3 and sizes[rel] > 1.8 * median:
            roles[rel] = "extra"
        else:
            roles[rel] = "episode"
    return roles


def _job_roles(item_id, base):
    """{file relative to the item: role from the title analysis} for a folder an A2RM job filed"""
    job_id = tracked_jobs().get(item_id.strip("/"))
    if job_id is None:
        return {}
    known = {}
    for entry in store.load_job(job_id).get("files") or []:
        path = os.path.normpath(entry.get("file") or "")
        if path.startswith(os.path.normpath(base) + os.sep):
            known[os.path.relpath(path, base).replace("\\", "/")] = entry.get("role") or "extra"
    return known


def _role_for(role, kind):
    if kind == "movie":
        return {"main": "main", "cut": "version", "language": "version", "feature": "version",
                "episode": "version"}.get(role, "keep")
    return "episode" if role == "episode" else "keep"


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def _episode_order(rel):
    """Sort episodes by their current SxxEyy, else MakeMKV's title number, else name"""
    name = os.path.basename(rel)
    numbered = EPISODE_NAME.search(name)
    if numbered:
        return 0, int(numbered.group(1)), int(numbered.group(2)), name
    tid = TID.search(name)
    return 1, 0, int(tid.group(1)) if tid else 999, name


def _clean_values(values):
    kind = values.get("kind")
    if kind not in KIND_ROOT:
        raise LibraryError("Pick Movie or TV show.")
    title = re.sub(r"\s+", " ", str(values.get("title") or "")).strip()
    if not title:
        raise LibraryError("Enter a title.")
    year = str(values.get("year") or "").strip()
    if year and not re.fullmatch(r"\d{4}", year):
        raise LibraryError("The year should be 4 digits (or empty).")
    imdb_id = str(values.get("imdb_id") or "").strip()
    if imdb_id and not re.fullmatch(r"tt\d{6,9}", imdb_id):
        raise LibraryError("The IMDb id looks like tt1234567 (or leave it empty).")
    season = first = None
    if kind == "series" and str(values.get("season") or "").strip():
        try:
            season = int(values["season"])
            first = int(values.get("first_episode") or 1)
        except (TypeError, ValueError) as error:
            raise LibraryError("Season and first episode are numbers.") from error
        if season < 0 or first < 1:
            raise LibraryError("Season is 0 or more, the first episode 1 or more.")
    return {"kind": kind, "title": title, "year": year, "imdb_id": imdb_id, "season": season, "first_episode": first}


def plan(item_id, values, roles=None, editions=None):
    """
    What renaming/moving this item would do.
    :param values: kind (movie|series), title, year, imdb_id, season, first_episode (TV)
    :param roles: {file relative to the item: main|version|episode|extra|keep}
    :param editions: {file relative to the item: label} for movie files (C40); no label = main gets none,
        another version keeps the one in its name (or "Version N")
    :return: {"item", "target", "moves": [{"from", "to", "role"}], "same": [...], "notes": [...]}
    """
    from arm.automation import naming
    values = _clean_values(values)
    source = absolute(item_id)
    if item_of(source) != item_id.strip("/") or not os.path.exists(source):
        raise LibraryError(f"{item_id} isn't in the library any more. Rescan.")
    base = source if os.path.isdir(source) else os.path.dirname(source)
    target_dir = os.path.join(completed(), KIND_ROOT[values["kind"]], folder_name(values["title"], values["year"]))
    job = _fake_job(values["title"], values["year"], values["kind"], values["imdb_id"])
    videos = _contents(source)[0]
    chosen = roles or {}
    roles = default_roles(item_id, values["kind"])
    roles.update({rel: role for rel, role in chosen.items() if rel in roles and role in ROLES})
    old_stem = os.path.splitext(os.path.basename(source))[0] if os.path.isfile(source) else os.path.basename(source)
    old_stem = COPY_SUFFIX.sub("", old_stem)
    if values["kind"] == "movie":
        editions = clean_editions(editions)
        names = _movie_names(videos, roles, job, old_stem, naming, editions)
    else:
        editions = {}
        names = _tv_names(videos, roles, job, values, naming)
    notes = []
    if values["kind"] == "series" and values["season"] is None and any(r == "episode" for r in roles.values()):
        notes.append("No season given: episode files keep their names.")
    if os.path.isdir(target_dir) and os.path.normpath(target_dir) != os.path.normpath(base):
        notes.append(f"{relative(target_dir)} is already there: the files go into it.")
    moves, same, drop = [], [], []
    taken = set()
    for rel in videos:
        target = os.path.normpath(os.path.join(target_dir, names.get(rel, rel)))
        _add_move(os.path.join(base, rel), target, roles.get(rel, "keep"), moves, same, taken)
        for side, side_target in _side_files(os.path.join(base, rel), target):
            _add_move(side, side_target, "with " + os.path.basename(rel), moves, same, taken)
    if os.path.isdir(source):
        _other_files(source, target_dir, (moves, same, drop, notes, taken))
    return {"item": item_id.strip("/"), "values": values, "roles": roles, "editions": editions,
            "target": relative(target_dir),
            "moves": moves, "same": same, "drop": drop, "notes": notes}


def _other_files(source, target_dir, lists):
    """Non-video files keep their place in the folder; a movie.nfo/tvshow.nfo never replaces another"""
    from arm.automation import nfo
    moves, same, drop, notes, taken = lists
    moved = {m["from"] for m in moves} | {s["from"] for s in same}
    for root, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            full = os.path.normpath(os.path.join(root, name))
            if relative(full) in moved or is_video(name):
                continue
            target = os.path.join(target_dir, os.path.relpath(full, source))
            clash = os.path.exists(target) and os.path.normpath(target) != full
            if name.lower() in ("movie.nfo", "tvshow.nfo") and clash:
                if nfo.ours(full):
                    drop.append(relative(full))  # ours: the folder it goes into has one already
                else:
                    notes.append(f"{relative(full)} stays where it is: {relative(target_dir)} has its own {name}.")
                continue
            _add_move(full, target, "other file", moves, same, taken)


def _movie_names(videos, roles, job, old_stem, naming, editions=None):
    names, editions = {}, editions or {}
    extras = str(cfg.arm_config.get("EXTRAS_SUB") or "extras")
    for rel in videos:
        role, name = roles.get(rel, "keep"), os.path.basename(rel)
        ext = os.path.splitext(name)[1]
        label = editions.get(rel)
        if role == "main":
            names[rel] = naming.movie_name(job, label or "") + ext
        elif role == "version":
            names[rel] = naming.movie_name(job, label or _edition(os.path.splitext(name)[0], old_stem, name)) + ext
        elif role == "extra":
            names[rel] = f"{extras}/{name}"
    return names


def clean_editions(editions):
    """{file: label} with empty labels dropped and each label made safe for a file name"""
    clean = {}
    for rel, label in (editions or {}).items():
        label = re.sub(r'[\\/:*?"<>|]+', " ", str(label or ""))
        label = re.sub(r"\s+", " ", label).strip(" .-")[:EDITION_MAX].strip()
        if label:
            clean[rel] = label
    return clean


def current_editions(item_id, roles):
    """{file: label already in its name} for the main movie and other versions, to start the form with"""
    source = absolute(item_id)
    old_stem = os.path.splitext(os.path.basename(source))[0] if os.path.isfile(source) else os.path.basename(source)
    old_stem = COPY_SUFFIX.sub("", old_stem)
    labels = {}
    for rel, role in roles.items():
        if role not in ("main", "version"):
            continue
        stem = os.path.splitext(os.path.basename(rel))[0]
        label = _edition(stem, old_stem, os.path.basename(rel))
        if label != stem and not TID.search(os.path.basename(rel)):
            labels[rel] = label
    return labels


def _edition(stem, old_stem, name):
    """'Annabelle- Creation (2017) - Shorter cut (-11 min)' -> 'Shorter cut (-11 min)'"""
    if old_stem and stem.startswith(old_stem) and stem[len(old_stem):].strip(" -"):
        return stem[len(old_stem):].strip(" -")
    if " - " in stem:
        return stem.split(" - ", 1)[1].strip()
    tid = TID.search(name)
    return f"Version {int(tid.group(1))}" if tid else stem


def _tv_names(videos, roles, job, values, naming):
    names = {}
    extras = str(cfg.arm_config.get("EXTRAS_SUB") or "extras")
    episodes = sorted((rel for rel in videos if roles.get(rel) == "episode"), key=_episode_order)
    if values["season"] is not None:
        for offset, rel in enumerate(episodes):
            number = values["first_episode"] + offset
            ext = os.path.splitext(rel)[1]
            names[rel] = f"{naming.season_folder(values['season'])}/" \
                         f"{naming.episode_name(job, values['season'], number)}{ext}"
    for rel in videos:
        if roles.get(rel) == "extra":
            names[rel] = f"{extras}/{os.path.basename(rel)}"
    return names


def _side_files(video, target):
    """Subtitles/images named after a video move and rename with it (Movie.en.srt, Movie-poster.jpg)"""
    folder, stem = os.path.dirname(video), os.path.splitext(os.path.basename(video))[0]
    new_stem = os.path.splitext(os.path.basename(target))[0]
    if not os.path.isdir(folder):
        return []
    found = []
    for name in sorted(os.listdir(folder)):
        if name.startswith(stem) and not is_video(name) and name.lower().endswith(SIDE_FILES) and \
                name[len(stem):len(stem) + 1] in (".", "-"):
            new_name = new_stem + name[len(stem):]
            found.append((os.path.join(folder, name), os.path.join(os.path.dirname(target), new_name)))
    return found


def _add_move(source, target, role, moves, same, taken):
    source, target = os.path.normpath(source), os.path.normpath(target)
    if any(m["from"] == relative(source) for m in moves):
        return
    if source == target:
        return
    if os.path.exists(target) and os.path.normcase(target) != os.path.normcase(source):
        if os.path.isfile(target) and _size(target) == _size(source):
            same.append({"from": relative(source), "to": relative(target), "role": role})
            return
        stem, ext = os.path.splitext(target)
        number = 2
        while os.path.exists(f"{stem} ({number}){ext}") or f"{stem} ({number}){ext}" in taken:
            number += 1
        target = f"{stem} ({number}){ext}"
    while target in taken:
        stem, ext = os.path.splitext(target)
        target = f"{stem} (2){ext}"
    taken.add(target)
    moves.append({"from": relative(source), "to": relative(target), "role": role})


# ---------------------------------------------------------------- apply / trash / undo

def apply(item_id, values, roles=None, editions=None):
    """Do the plan (worked out again here, not trusted from the page). Returns the log entry."""
    result = plan(item_id, values, roles, editions)
    check_not_in_use([item_id, result["target"]], [title_key(result["values"]["title"], result["values"]["year"])])
    if not result["moves"]:
        raise LibraryError("Nothing to change: the names are already right.")
    source = absolute(item_id)
    done = _move_all(result["moves"])
    target_dir = absolute(result["target"])
    for rel in result["drop"]:
        os.remove(absolute(rel))  # an A2RM-written .nfo; the target folder has its own
    _remove_empty(source if os.path.isdir(source) else None)
    jobs = _update_jobs(item_id, result, done)
    nfo_written = _write_nfo(result["values"], target_dir)
    _announce(target_dir, source)
    values = result["values"]
    entry = _log("rename", f"{item_id} → {result['target']}", done, jobs,
                 left=[s["from"] for s in result["same"]])
    logging.info(f"Library: {item_id} -> {result['target']}: moved {len(done)} file(s), "
                 f"{len(result['same'])} already there; jobs {[j['job_id'] for j in jobs] or 'none'}; "
                 f"NFO {'written' if nfo_written else 'not written'} ({values['title']})")
    return entry


def _move_all(moves):
    done = []
    try:
        for move in moves:
            source, target = absolute(move["from"]), absolute(move["to"])
            if not os.path.exists(source):
                raise LibraryError(f"{move['from']} has gone.")
            if os.path.exists(target):
                raise LibraryError(f"{move['to']} appeared meanwhile; nothing overwritten.")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(source, target)
            done.append([move["from"], move["to"]])
    except Exception:
        _reverse(done)
        raise
    return done


def _reverse(done):
    for source, target in reversed(done):
        try:
            os.makedirs(os.path.dirname(absolute(source)), exist_ok=True)
            shutil.move(absolute(target), absolute(source))
        except OSError as error:
            logging.error(f"Library: couldn't move {target} back to {source}: {error}")
    for _source, target in done:
        _remove_empty(os.path.dirname(absolute(target)), stop=True)


def _remove_empty(folder, stop=False):
    """Remove folders left empty (bottom-up), never the movies/tv/unidentified folders themselves"""
    if not folder or not os.path.isdir(folder):
        return
    roots = {os.path.join(completed(), root) for root in ROOTS} | {completed()}
    for root, _dirs, _files in sorted(os.walk(folder), key=lambda entry: -len(entry[0])):
        if root not in roots and not os.listdir(root):
            os.rmdir(root)
    while stop and folder not in roots and os.path.isdir(folder) and not os.listdir(folder):
        os.rmdir(folder)
        folder = os.path.dirname(folder)


def _update_jobs(item_id, result, done):
    """Jobs that owned the item: new folder, title, type and file list. Returns what they were before."""
    moved = {absolute(a): absolute(b) for a, b in done}
    before = []
    owners = [job_id for item, job_id in tracked_jobs().items() if item == item_id.strip("/")]
    if not owners:
        return before
    from arm.models.job import Job
    from arm.ui import db
    values, target_dir = result["values"], absolute(result["target"])
    for job_id in owners:
        job = Job.query.get(job_id)
        if job is None:
            continue
        before.append({"job_id": job_id, "path": job.path, "title_manual": job.title_manual,
                       "year_manual": job.year_manual, "video_type_manual": job.video_type_manual,
                       "imdb_id_manual": job.imdb_id_manual, "title": job.title, "year": job.year,
                       "video_type": job.video_type, "imdb_id": job.imdb_id})
        job.path = target_dir
        job.title = job.title_manual = values["title"]
        job.year = job.year_manual = values["year"] or job.year
        job.video_type = job.video_type_manual = values["kind"]
        if values["imdb_id"]:
            job.imdb_id = job.imdb_id_manual = values["imdb_id"]
        job.hasnicetitle = True
        data = store.load_job(job_id)
        files = [dict(entry, file=moved.get(os.path.normpath(entry.get("file") or ""), entry.get("file")))
                 for entry in data.get("files") or []]
        update = {"files": files} if files else {}
        if values["kind"] == "series" and values["season"] is not None:
            update["tv_season"] = {"season": values["season"], "first_episode": values["first_episode"]}
        if data.get("unidentified"):
            update["unidentified"] = None
            update["confidence"] = dict(data.get("confidence") or {}, confirmed=True)
        if update:
            store.update_job(job_id, **update)
        store.clear_attention(job_id, "confidence")
        store.clear_attention(job_id, "episodes")
    db.session.commit()
    return before


def _write_nfo(values, folder):
    if not values["imdb_id"]:
        return None
    try:
        from arm.automation import nfo
        return nfo.write(_fake_job(values["title"], values["year"], values["kind"], values["imdb_id"]), folder)
    except Exception as error:  # noqa: BLE001 - optional
        logging.warning(f"Library: couldn't write the NFO in {folder}: {error}")
        return None


def _announce(*paths):
    try:
        from arm.automation import jellyfin
        jellyfin.announce(None, *paths)
    except Exception as error:  # noqa: BLE001 - optional
        logging.info(f"Library: Jellyfin not told: {error}")


def trash(item_id):
    """Move an item to COMPLETED_PATH/.a2rm-trash/<time>/<item> (undo puts it back)"""
    source = absolute(item_id)
    if item_of(source) != item_id.strip("/") or not os.path.exists(source):
        raise LibraryError(f"{item_id} isn't in the library any more. Rescan.")
    check_not_in_use([item_id])
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(completed(), TRASH, stamp, item_id.strip("/"))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.move(source, target)
    _announce(source)
    logging.info(f"Library: moved {item_id} to the trash ({relative(target)})")
    return _log("trash", f"{item_id} → trash", [[item_id.strip("/"), relative(target)]], [])


def trash_size():
    """(items, bytes) in the trash"""
    folder = os.path.join(completed(), TRASH)
    count = total = 0
    if os.path.isdir(folder):
        for stamp in os.listdir(folder):
            for root in ROOTS:
                path = os.path.join(folder, stamp, root)
                count += len(os.listdir(path)) if os.path.isdir(path) else 0
        for root, _dirs, files in os.walk(folder):
            total += sum(max(0, _size(os.path.join(root, name))) for name in files)
    return count, total


def empty_trash():
    """Permanently delete the trash (the page asks first). Returns the number of items removed."""
    count, _total = trash_size()
    folder = os.path.join(completed(), TRASH)
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    entries = [dict(e, undone=True) if e["action"] == "trash" and not e.get("undone") else e for e in history()]
    store.update_state(library_log=entries)
    logging.info(f"Library: emptied the trash ({count} item(s))")
    return count


def history():
    return store.load_state().get("library_log") or []


def _log(action, summary, moves, jobs, left=None):
    entries = history()
    entry = {"id": max((e["id"] for e in entries), default=0) + 1, "action": action, "summary": summary,
             "time": datetime.datetime.now().isoformat(timespec="seconds"), "moves": moves, "jobs": jobs,
             "left": left or []}
    store.update_state(library_log=([entry] + entries)[:LOG_SIZE])
    return entry


def undo(entry_id):
    """Put the files of one change back where they were and the jobs back as they were"""
    entries = history()
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if entry is None or entry.get("undone"):
        raise LibraryError("That change can't be undone (already undone, or too old).")
    check_not_in_use({item_of(absolute(rel)) for move in entry["moves"] for rel in move})
    for source, target in entry["moves"]:
        if not os.path.exists(absolute(target)):
            raise LibraryError(f"{target} isn't there any more; nothing was changed.")
        if os.path.exists(absolute(source)):
            raise LibraryError(f"Something is at {source} again; nothing was changed.")
    _reverse(entry["moves"])
    if entry["jobs"]:
        from arm.models.job import Job
        from arm.ui import db
        moved = {os.path.normpath(absolute(b)): absolute(a) for a, b in entry["moves"]}
        for before in entry["jobs"]:
            job = Job.query.get(before["job_id"])
            if job is None:
                continue
            for key, value in before.items():
                if key != "job_id":
                    setattr(job, key, value)
            data = store.load_job(job.job_id)
            if data.get("files"):
                files = [dict(f, file=moved.get(os.path.normpath(f.get("file") or ""), f.get("file")))
                         for f in data["files"]]
                store.update_job(job.job_id, files=files)
        db.session.commit()
    _announce(*{os.path.dirname(absolute(a)) for a, _b in entry["moves"]})
    store.update_state(library_log=[dict(e, undone=True) if e["id"] == entry_id else e for e in entries])
    logging.info(f"Library: undid change {entry_id} ({entry['summary']})")
    return entry


# ---------------------------------------------------------------- running jobs (C42)

FINISHED = ("success", "fail")


def _alive(job):
    """The job's own ripper process is still running (a reused PID has another hash)"""
    import psutil
    try:
        return bool(job.pid) and hash(psutil.Process(job.pid)) == job.pid_hash
    except (psutil.Error, TypeError):
        return False


def running_jobs():
    """Jobs still ripping, transcoding, waiting or identifying: what they're called and where they file"""
    jobs = []
    try:
        from arm.models.job import Job
        from arm.ui import db
        try:
            for job in Job.query.filter(Job.status.notin_(FINISHED)).all():
                if not _alive(job):
                    continue  # a job left "active" by a crash isn't using anything
                title = job.title_manual or job.title or ""
                year = job.year if job.year and job.year not in ("0000", "None") else ""
                name = f"{title} ({year})" if year and title else title or job.label
                jobs.append({"job_id": job.job_id, "name": name,
                             "key": title_key(title, year) if title else "",
                             "item": item_of(job.path) if job.path else None})
        finally:
            db.session.rollback()  # end the read so the ripper can write
    except Exception as error:  # noqa: BLE001 - can't tell: be careful
        logging.info(f"Library: couldn't read the running jobs: {error}")
        return [{"job_id": "?", "name": "a running job", "key": "", "item": None, "unknown": True}]
    return jobs


def _item_key(item_id):
    parsed = parse_name(os.path.basename(str(item_id).strip("/")))
    return title_key(parsed["title"], parsed["year"]) if parsed["title"] else ""


def check_not_in_use(item_ids, keys=()):
    """
    Refuse a change that touches a folder a running job is using or will file into (C42): its own folder,
    an unidentified/<label> (job N) folder, or a folder with the same title and year. Everything else is
    fine while a disc rips: these are moves inside the completed library, not the rip's own folders.
    """
    item_ids = [str(i).strip("/") for i in item_ids if i]
    keys = {k for k in keys if k} | {_item_key(i) for i in item_ids}
    keys.discard("")
    for job in running_jobs():
        if job.get("unknown"):
            raise LibraryError("Couldn't check which jobs are running; try again in a moment.")
        hit = next((i for i in item_ids if i == job["item"] or f"(job {job['job_id']})" in i), None)
        if hit or (job["key"] and job["key"] in keys):
            where = hit or job["item"] or "a folder with that title"
            raise LibraryError(f"Job {job['job_id']} ({job['name']}) is still running and uses {where}. "
                               "Try again when it's done.")
