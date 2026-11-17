"""
Configurable names (MOVIE_NAME_PATTERN, EPISODE_NAME_PATTERN, SEASON_FOLDER_PATTERN), like
Radarr/Sonarr naming formats. Used when A2RM organizes a disc's files (title analysis) and on
a retitle. The defaults give the names A2RM always used. The movie/show FOLDER stays ARM's
"Title (Year)" (duplicate checks and retitles rely on it).

Tokens: {Title} {Year} {Edition} {ImdbId} for movies; {Show} {Year} {Season} {Episode}
{EpisodeTitle} {ImdbId} for TV. Numbers can be padded: {Season:00}, {Episode:000}. Empty tokens
are dropped with their brackets: "{Title} ({Year})" without a year gives "Title".
"""
import logging
import re

from arm.automation import settings

TOKEN = re.compile(r"\{(\w+)(?::(0+))?\}")
EMPTY_BRACKETS = re.compile(r"\(\s*\)|\[\s*\]")
DANGLING = re.compile(r"(\s*-\s*){2,}")
MOVIE_TOKENS = ("Title", "Year", "Edition", "ImdbId")
EPISODE_TOKENS = ("Show", "Year", "Season", "Episode", "EpisodeTitle", "ImdbId")
SAMPLE_MOVIE = {"Title": "Fast Five", "Year": "2011", "Edition": "", "ImdbId": "tt1596343"}
SAMPLE_EPISODE = {"Show": "Doctor Who", "Year": "2005", "Season": 11, "Episode": 1,
                  "EpisodeTitle": "The Woman Who Fell to Earth", "ImdbId": "tt0436992"}


def render(pattern, values):
    """Fill the tokens, drop empty ones with their brackets, tidy separators. Unknown tokens become empty."""
    def fill(match):
        name, pad = match.group(1), match.group(2)
        if name not in values:
            logging.info(f"Unknown naming token {{{name}}}")
            return ""
        value = values[name]
        if value in (None, ""):
            return ""
        if pad and str(value).isdigit():
            return str(value).zfill(len(pad))
        return str(value)
    text = TOKEN.sub(fill, str(pattern or ""))
    text = EMPTY_BRACKETS.sub("", text)
    text = DANGLING.sub(" - ", text)
    return re.sub(r"\s+", " ", text).strip(" -._")


def _year(job):
    year = str(getattr(job, "year", "") or "")
    return year if year and year != "0000" else ""


def _safe(name, fallback):
    from arm.automation.organize import safe_name
    return safe_name(name) if name else safe_name(fallback)


def movie_name(job, edition=""):
    """Main movie file name (no extension); other versions add the edition"""
    pattern = settings.get("MOVIE_NAME_PATTERN")
    values = {"Title": job.title_manual or job.title, "Year": _year(job), "Edition": edition,
              "ImdbId": job.imdb_id or ""}
    name = render(pattern, values)
    if edition and "{Edition" not in pattern:
        name = f"{name} - {edition}" if name else edition
    default = render(settings.BY_KEY["MOVIE_NAME_PATTERN"].default, values)
    return _safe(name, f"{default} - {edition}" if edition else default)


def episode_title(job, season, number):
    from arm.automation import tmdb
    found = tmdb.find_by_imdb(job.imdb_id)
    if not found or found[0] != "tv":
        return ""
    for episode in (tmdb.season(found[1], season) or {}).get("episodes") or []:
        if episode.get("episode_number") == number:
            return episode.get("name") or ""
    return ""


def episode_name(job, season, number):
    pattern = settings.get("EPISODE_NAME_PATTERN")
    values = {"Show": job.title_manual or job.title, "Year": _year(job), "Season": season, "Episode": number,
              "EpisodeTitle": episode_title(job, season, number) if "{EpisodeTitle" in pattern else "",
              "ImdbId": job.imdb_id or ""}
    default = render(settings.BY_KEY["EPISODE_NAME_PATTERN"].default, values)
    return _safe(render(pattern, values), default)


def season_folder(season):
    values = {"Season": season}
    return _safe(render(settings.get("SEASON_FOLDER_PATTERN"), values),
                 render(settings.BY_KEY["SEASON_FOLDER_PATTERN"].default, values))


def preview(kind, pattern):
    """Sample name for Settings: kind = movie | edition | episode | season"""
    if kind == "season":
        return render(pattern, {"Season": SAMPLE_EPISODE["Season"]}) + "/"
    if kind == "episode":
        return render(pattern, SAMPLE_EPISODE) + ".mkv"
    name = render(pattern, SAMPLE_MOVIE) + ".mkv"
    edition = dict(SAMPLE_MOVIE, Edition="Extended cut (+12 min)")
    extended = render(pattern, edition) if "{Edition" in pattern else \
        render(pattern, SAMPLE_MOVIE) + " - " + edition["Edition"]
    return f"{name} · {extended}.mkv"
