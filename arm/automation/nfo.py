"""
Minimal Kodi/Jellyfin .nfo files (WRITE_NFO): when A2RM files a movie or TV show it knows the
IMDb id, and from it the TMDb id. A `movie.nfo` / `tvshow.nfo` with those ids makes Jellyfin
match exactly the film ARM ripped (no wrong-film matches) and helps the TMDb Box Sets plugin.
Only title, year and the ids are written; Jellyfin still fetches everything else.
Files A2RM didn't write are never touched. On a retitle the NFO is rewritten.

Optional (TMM_URL + TMM_API_KEY): ask tinyMediaManager's HTTP API to update its library and
scrape the new movie/show, so artwork and full NFOs come from the tool you already run.
"""
import logging
import os
from xml.sax.saxutils import escape

import requests

from arm.automation import settings

MARK = "<!-- written by A2RM -->"


def _ids(job):
    from arm.automation import tmdb
    imdb_id = str(job.imdb_id or "")
    if not imdb_id.startswith("tt"):
        return None, None
    found = tmdb.find_by_imdb(imdb_id)
    return imdb_id, (str(found[1]) if found else None)


def render(job, imdb_id, tmdb_id):
    series = job.video_type == "series"
    root = "tvshow" if series else "movie"
    title = job.title_manual or job.title or ""
    year = str(job.year or "")
    lines = ['<?xml version="1.0" encoding="utf-8" standalone="yes"?>', MARK, f"<{root}>",
             f"  <title>{escape(title)}</title>"]
    if year and year != "0000":
        lines.append(f"  <year>{escape(year[:4])}</year>")
    if tmdb_id:
        lines.append(f'  <uniqueid type="tmdb" default="true">{escape(tmdb_id)}</uniqueid>')
        lines.append(f"  <tmdbid>{escape(tmdb_id)}</tmdbid>")
    default = "" if tmdb_id else ' default="true"'
    imdb_tag = "imdb_id" if series else "imdbid"
    lines.append(f'  <uniqueid type="imdb"{default}>{escape(imdb_id)}</uniqueid>')
    lines.append(f"  <{imdb_tag}>{escape(imdb_id)}</{imdb_tag}>")
    lines.append(f"</{root}>")
    return "\n".join(lines) + "\n"


def nfo_path(job, folder):
    return os.path.join(folder, "tvshow.nfo" if job.video_type == "series" else "movie.nfo")


def ours(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return MARK in handle.read(400)
    except OSError:
        return False


def write(job, folder):
    """Write (or rewrite our own) NFO in the job's folder. Returns its path or None."""
    if not settings.get("WRITE_NFO") or not folder or not os.path.isdir(folder):
        return None
    if job.video_type not in ("movie", "series") or "/unidentified/" in str(folder).replace("\\", "/"):
        return None
    imdb_id, tmdb_id = _ids(job)
    if not imdb_id:
        return None
    path = nfo_path(job, folder)
    if os.path.exists(path) and not ours(path):
        logging.info(f"{path} wasn't written by A2RM; leaving it alone")
        return None
    other = nfo_path(type("J", (), {"video_type": "movie" if job.video_type == "series" else "series"})(), folder)
    if os.path.exists(other) and ours(other):
        os.remove(other)  # the job switched between movie and TV show
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render(job, imdb_id, tmdb_id))
    logging.info(f"Wrote {path} (TMDb {tmdb_id or 'unknown'}, IMDb {imdb_id})")
    tmm_scrape(job)
    return path


def tmm_scrape(job):
    """tinyMediaManager: update the data sources and scrape new items. True if tMM accepted."""
    url, key = settings.get("TMM_URL").rstrip("/"), settings.get("TMM_API_KEY")
    if not url or not key:
        return False
    endpoint = f"{url}/api/{'tvshow' if job.video_type == 'series' else 'movie'}"
    commands = [{"action": "update", "scope": {"name": "all"}}, {"action": "scrape", "scope": {"name": "new"}}]
    try:
        response = requests.post(endpoint, json=commands, headers={"api-key": key}, timeout=20)
    except requests.RequestException as error:
        logging.warning(f"Couldn't reach tinyMediaManager: {error}")
        return False
    if response.status_code >= 300:
        logging.warning(f"tinyMediaManager didn't accept the scrape: HTTP {response.status_code}")
        return False
    logging.info("Asked tinyMediaManager to update and scrape new items")
    return True
