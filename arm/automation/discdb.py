"""
TheDiscDB (https://thediscdb.com): a community database that says what each title on a disc is
(main movie, extra, episode with season and number).

A disc is found by its "content hash": MD5 over the sizes of the disc's files, each size as 8
little-endian bytes, in file-name order, as 32 upper-case hex characters (TheDiscDb/web,
HashingExtensions.CalculateHash). DVDs: every file in VIDEO_TS. Blu-rays: the .m2ts files in
BDMV/STREAM. The hash is computed locally while the disc is mounted; only the hash is sent,
and only when DISCDB_LOOKUP is on (off by default).

On a hit: the title (if you didn't set one), a top confidence signal, the title roles and the
TV episode numbers.
"""
import hashlib
import logging
import os
import re
import struct

import requests

from arm.automation import settings, store

HASH = re.compile(r"^[0-9A-F]{32}$")
EPISODE_TYPES = ("episode",)
MAIN_TYPES = ("mainmovie", "main movie", "movie")


def _find_dir(root, *parts):
    """Case-insensitive path below root, or None"""
    path = root
    for part in parts:
        try:
            match = next((name for name in os.listdir(path) if name.lower() == part.lower()), None)
        except OSError:
            return None
        if match is None:
            return None
        path = os.path.join(path, match)
    return path if os.path.isdir(path) else None


def disc_files(mountpoint, disctype):
    """[(name, size)] of the files TheDiscDB hashes, in name order"""
    if not mountpoint:
        return []
    if disctype == "bluray":
        folder = _find_dir(mountpoint, "BDMV", "STREAM")
        wanted = lambda name: name.lower().endswith(".m2ts")  # noqa: E731
    else:
        folder = _find_dir(mountpoint, "VIDEO_TS")
        wanted = lambda name: True  # noqa: E731
    if not folder:
        return []
    files = []
    for name in sorted(os.listdir(folder), key=str.upper):
        path = os.path.join(folder, name)
        if wanted(name) and os.path.isfile(path):
            files.append((name, os.path.getsize(path)))
    return files


def content_hash(sizes):
    """MD5 over the sizes (int64 little-endian), upper-case hex"""
    digest = hashlib.md5(usedforsecurity=False)
    for size in sizes:
        digest.update(struct.pack("<q", int(size)))
    return digest.hexdigest().upper()


def compute(job):
    """While the disc is mounted: compute and save its hash. Local only; returns the hash or None."""
    if not settings.get("DISCDB_LOOKUP") or job.disctype not in ("dvd", "bluray"):
        return None
    files = disc_files(getattr(job, "mountpoint", None), job.disctype)
    if not files:
        logging.info("TheDiscDB: no disc files to hash")
        return None
    value = content_hash(size for _name, size in files)
    logging.info(f"TheDiscDB content hash: {value} ({len(files)} files)")
    store.update_job(job.job_id, discdb={"hash": value})
    return value


QUERY = """
query {
  mediaItems(where: {releases: {some: {discs: {some: {contentHash: {eq: "%s"}}}}}}) {
    nodes {
      title year type slug
      externalids { tmdb imdb }
      releases {
        title year slug
        discs {
          contentHash index name format
          titles { index duration sourceFile size item { title season episode type } }
        }
      }
    }
  }
}
"""


def _seconds(value):
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).split(".")[0]
    total = 0
    for part in text.split(":"):
        if not part.strip().isdigit():
            return 0
        total = total * 60 + int(part)
    return total


def parse(data, value):
    """The GraphQL answer -> {title, year, type, imdb, tmdb, release, disc, titles: [...]} or None"""
    nodes = (((data or {}).get("data") or {}).get("mediaItems") or {}).get("nodes") or []
    for node in nodes:
        for release in node.get("releases") or []:
            for disc in release.get("discs") or []:
                if str(disc.get("contentHash") or "").upper() != value:
                    continue
                external = node.get("externalids") or {}
                titles = []
                for entry in disc.get("titles") or []:
                    item = entry.get("item") or {}
                    titles.append({"index": entry.get("index"), "seconds": _seconds(entry.get("duration")),
                                   "source": str(entry.get("sourceFile") or ""),
                                   "type": str(item.get("type") or ""), "name": str(item.get("title") or ""),
                                   "season": _int(item.get("season")), "episode": _int(item.get("episode"))})
                return {"title": node.get("title"), "year": str(node.get("year") or ""),
                        "type": "series" if str(node.get("type") or "").lower() == "series" else "movie",
                        "imdb": external.get("imdb") or "", "tmdb": external.get("tmdb") or "",
                        "slug": node.get("slug") or "", "release": release.get("title") or "",
                        "disc": disc.get("name") or "", "titles": titles}
    return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def query(value):
    """Ask TheDiscDB about a hash. None when it isn't known or the lookup fails."""
    if not HASH.match(str(value or "")):
        return None
    try:
        response = requests.post(settings.get("DISCDB_URL"), json={"query": QUERY % value}, timeout=15,
                                 headers={"Content-Type": "application/json"})
        if response.status_code != 200:
            logging.info(f"TheDiscDB lookup: HTTP {response.status_code}")
            return None
        return parse(response.json(), value)
    except (requests.RequestException, ValueError) as error:
        logging.info(f"TheDiscDB lookup failed: {error}")
        return None


def lookup(job):
    """
    After identification: look the saved hash up; on a hit use its title unless you set one.
    :return: the hit or None
    """
    if not settings.get("DISCDB_LOOKUP"):
        return None
    saved = store.load_job(job.job_id).get("discdb") or {}
    if not saved.get("hash"):
        return None
    hit = query(saved["hash"])
    store.update_job(job.job_id, discdb=dict(saved, hit=hit, checked=True))
    if not hit:
        logging.info("TheDiscDB doesn't know this disc")
        return None
    logging.info(f"TheDiscDB: {hit['title']} ({hit['year']}), {hit['release']} {hit['disc']}".strip())
    if not job.title_manual and hit.get("imdb") and hit["title"] and str(job.imdb_id or "") != hit["imdb"]:
        from arm.automation import confidence
        from arm.ripper import utils
        logging.info(f"Using TheDiscDB's title instead of '{job.title}'")
        confidence.adopt(job, utils.clean_for_filename(hit["title"]), hit["year"], hit["imdb"], None, hit["type"])
    return hit


def hit_for(job):
    """The saved hit when it matches the job's current IMDb id (so it still describes this title)"""
    if not settings.get("DISCDB_LOOKUP"):
        return None
    hit = (store.load_job(job.job_id).get("discdb") or {}).get("hit")
    if not hit or not hit.get("imdb") or str(job.imdb_id or "") != hit["imdb"]:
        return None
    return hit


def _match(hit_titles, title):
    """TheDiscDB's entry for a MakeMKV title: same source file, else same index and length, else same length"""
    source = str(title.source or "").lower()
    if source:
        found = [t for t in hit_titles if t["source"].lower() == source]
        if len(found) == 1:
            return found[0]
    found = [t for t in hit_titles if t["index"] == title.tid and abs(t["seconds"] - title.seconds) <= 3]
    if found:
        return found[0]
    found = [t for t in hit_titles if t["seconds"] and abs(t["seconds"] - title.seconds) <= 2]
    return found[0] if len(found) == 1 else None


def apply(job, titles, decisions):
    """
    Title roles from TheDiscDB: episodes get their season/episode, the main movie becomes main,
    named extras get their names. Titles TheDiscDB doesn't list keep the analysis' decision.
    :return: number of titles changed
    """
    hit = hit_for(job)
    if not hit or not hit.get("titles"):
        return 0
    changed = 0
    by_tid = {t.tid: t for t in titles}
    old_main = next((d for d in decisions.values() if d["role"] == "main"), None)
    for tid, decision in decisions.items():
        title = by_tid.get(tid)
        entry = _match(hit["titles"], title) if title else None
        if not entry or not entry["type"] or decision["role"] == "short":
            continue  # not on TheDiscDB, or listed without saying what it is
        kind = entry["type"].lower()
        note = f"TheDiscDB: {entry['type'] or 'listed'}" + (f" ({entry['name']})" if entry["name"] else "")
        if kind in EPISODE_TYPES and job.video_type == "series":
            decision.update(role="episode", rip=True, reason=note,
                            discdb_season=entry["season"], discdb_episode=entry["episode"])
        elif kind in MAIN_TYPES and job.video_type != "series":
            if old_main is not None and old_main is not decision:
                old_main.update(role="cut", label="Other version",
                                reason="TheDiscDB lists another title as the main movie",
                                rip=bool(settings.get("KEEP_ALTERNATE_CUTS")))
            decision.update(role="main", rip=True, reason=note, label="")
        elif decision["role"] in ("extra", "episode", "feature", "playall") and kind not in EPISODE_TYPES:
            decision.update(role="extra", rip=decision["rip"] or decision["role"] == "episode",
                            label=entry["name"] or decision["label"], reason=note)
        else:
            continue
        changed += 1
    if changed:
        logging.info(f"TheDiscDB set the role of {changed} title(s)")
    return changed


def episode_numbers(episodes):
    """{tid: (season, episode)} when TheDiscDB numbered every episode title, else {}"""
    numbers = {d["tid"]: (d.get("discdb_season"), d.get("discdb_episode")) for d in episodes}
    if not numbers or any(s is None or e is None for s, e in numbers.values()):
        return {}
    return numbers
