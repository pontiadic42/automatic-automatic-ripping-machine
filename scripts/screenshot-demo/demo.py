"""
A2RM screenshot demo: a throwaway web UI full of made-up discs, for the pictures on the GitHub page.

Nothing comes from a real install: the titles are invented, posters and stills are generated with ffmpeg,
the "library" is short generated video files, and TMDb, the hardware check and the server stats are
replaced with fixed demo values. Nothing is sent anywhere.

Run it in a throwaway container from the A2RM image (it writes only inside that container):

    docker run --rm -p 8095:8095 --entrypoint "" -e DEMO_URL=http://<host>:8095 \
        -v "$PWD/scripts/screenshot-demo:/demo:ro" arm:latest python3 /demo/demo.py

then open http://<host>:8095. DEMO_URL is the address your browser uses (search-result posters need a
full http address). See README.md next to this file for the screenshots taken from it.
"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = "/home/arm"  # inside the throwaway container, so paths look like a normal install
CACHE = "/tmp/a2rm-demo-cache"
PORT = int(os.environ.get("DEMO_PORT", "8095"))
DEMO_URL = os.environ.get("DEMO_URL", f"http://localhost:{PORT}").rstrip("/")
STATIC = "/opt/arm/arm/ui/static/demo"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_LIGHT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Invented titles: (title, year, kind, poster colours)
MOVIES = [
    ("Copper Valley", "2016", "movie", ("0x1d3557", "0xe76f51")),
    ("The Last Orchard", "2021", "movie", ("0x264653", "0xe9c46a")),
    ("Signal Lost", "2012", "movie", ("0x111827", "0x22d3ee")),
    ("Paper Moons", "2009", "movie", ("0x3d405b", "0xf2cc8f")),
    ("Northbound", "2018", "movie", ("0x0b3954", "0xbfd7ea")),
    ("Harbor Lights", "2011", "movie", ("0x2b2d42", "0xef233c")),
    ("Glass Garden", "2020", "movie", ("0x2d6a4f", "0xd8f3dc")),
    ("The Quiet Mile", "2015", "movie", ("0x463f3a", "0xe0afa0")),
    ("Starfield Station", "2015", "series", ("0x03045e", "0x90e0ef")),
    ("Copperhead Run", "2013", "movie", ("0x5f0f40", "0xfb8b24")),
    ("Copper Kettle Christmas", "2019", "movie", ("0x6a040f", "0xffba08")),
    ("Valley of Copper", "1987", "movie", ("0x582f0e", "0xb6ad90")),
]


def run(*command):
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------------------------------------------------------------- config

def write_config():
    os.makedirs("/etc/arm/config", exist_ok=True)
    for name in ("apprise.yaml", ".abcde.conf"):
        if os.path.exists(f"/opt/arm/setup/{name}"):
            shutil.copy(f"/opt/arm/setup/{name}", f"/etc/arm/config/{name.lstrip('.')}")
    with open("/opt/arm/setup/arm.yaml", encoding="utf-8") as handle:
        text = handle.read()
    values = {
        "DBFILE": f"{ROOT}/db/arm.db", "LOGPATH": f"{ROOT}/logs/", "RAW_PATH": f"{ROOT}/media/raw/",
        "TRANSCODE_PATH": f"{ROOT}/media/transcode/", "COMPLETED_PATH": f"{ROOT}/media/completed/",
        "DISABLE_LOGIN": "true", "WEBSERVER_PORT": str(PORT), "TMDB_API_KEY": "demo", "METADATA_PROVIDER": "tmdb",
        "NTFY_SERVER": "https://ntfy.example.com", "NTFY_TOPIC": "demo", "JELLYFIN_URL": "http://jellyfin.example:8096",
        "JELLYFIN_API_KEY": "demo", "DISCDB_LOOKUP": "true", "HB_ARGS_DVD": "--subtitle scan -F -e qsv_h264 --encoder-preset speed",
        "HB_ARGS_BD": "--subtitle scan -F -e qsv_h265 --encoder-preset speed",
    }
    for key, value in values.items():
        quoted = value if value in ("true", "false") or value.isdigit() else f'"{value}"'
        text, count = re.subn(rf"^{key}:.*$", f"{key}: {quoted}", text, flags=re.M)
        if not count:
            text += f"\n{key}: {quoted}\n"
    with open("/etc/arm/config/arm.yaml", "w", encoding="utf-8") as handle:
        handle.write(text)
    for folder in ("db", "logs/progress", "media/raw", "media/transcode", "media/completed"):
        os.makedirs(f"{ROOT}/{folder}", exist_ok=True)


# ---------------------------------------------------------------- pictures and videos

def poster(title, year, colours):
    """A 342x513 poster: a gradient, the title and the year"""
    os.makedirs(f"{STATIC}/posters", exist_ok=True)
    path = f"{STATIC}/posters/{slug(title)}.png"
    words, lines, line = title.upper().split(), [], ""
    for word in words:
        if len(line) + len(word) > 11 and line:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    text = []
    for number, line in enumerate(lines):
        y = 300 + number * 46 - len(lines) * 23
        text.append(f"drawtext=fontfile={FONT}:text='{line}':fontcolor=white:fontsize=38:x=(w-text_w)/2:y={y}:"
                    "shadowcolor=black@0.5:shadowx=2:shadowy=2")
    text.append(f"drawtext=fontfile={FONT_LIGHT}:text='{year}':fontcolor=white@0.85:fontsize=22:x=(w-text_w)/2:y=440")
    run("ffmpeg", "-y", "-f", "lavfi", "-i", f"gradients=s=342x513:c0={colours[0]}:c1={colours[1]}:x0=0:y0=0:x1=342:y1=513",
        "-vf", ",".join(text), "-frames:v", "1", path)
    return f"/static/demo/posters/{slug(title)}.png"


def video(path, seconds, colours, label="", gb=None):
    """
    A real, tiny video file of the given length (so lengths and stills work), grown to a disc-like size
    with a sparse tail (gb; default about 0.55 GB per 1,000 s), which takes no disk space
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cache = f"{CACHE}/{seconds}-{colours[0]}-{colours[1]}.mkv"
    if not os.path.exists(cache):
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        vf = f"drawtext=fontfile={FONT}:text='{label}':fontcolor=white@0.8:fontsize=18:x=12:y=h-30" if label else "null"
        run("ffmpeg", "-y", "-f", "lavfi", "-i",
            f"gradients=s=320x180:r=1:speed=0.004:c0={colours[0]}:c1={colours[1]}:c2=0x222222",
            "-t", str(seconds), "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", "-g", "60", cache)
    shutil.copy(cache, path)
    os.truncate(path, int((gb if gb is not None else seconds * 0.00055) * 1024 ** 3))


# ---------------------------------------------------------------- database and records

def seed():
    from arm.ui import app, db
    from arm.models.job import Job
    from arm.models.config import Config
    from arm.models.track import Track
    from arm.models.system_drives import SystemDrives
    from arm.models.ui_settings import UISettings
    from arm.automation import store
    import arm.config.config as cfg

    posters = {title: poster(title, year, colours) for title, year, _kind, colours in MOVIES}
    colours = {title: c for title, _y, _k, c in MOVIES}
    completed = cfg.arm_config["COMPLETED_PATH"]
    now = datetime.datetime.now()

    with app.app_context():
        db.create_all()
        db.session.add(UISettings(use_icons=True, save_remote_images=False, bootstrap_skin="spacelab",
                                  language="en", index_refresh=2000, database_limit=200, notify_refresh=6500))
        drives = []
        for number, (model, kind) in enumerate((("DVD-RW DH16NS40", "dvd"), ("BD-RE BU40N", "bluray"))):
            drive = SystemDrives()
            drive.name, drive.mount, drive.model, drive.maker = f"Drive {number + 1}", f"/dev/sr{number}", model, "Demo"
            drive.read_cd, drive.read_dvd, drive.read_bd = True, True, kind == "bluray"
            drive.stale, drive.drive_mode, drive.description = False, "auto", ""
            db.session.add(drive)
            drives.append(drive)
        db.session.commit()

        def job(title, status, days, label, disctype="bluray", kind="movie", path=None, year=None, poster_url=None,
                device="/dev/sr1", errors=None, title_value=None):
            row = Job.__mapper__.class_manager.new_instance()
            year = year if year is not None else next((y for t, y, _k, _c in MOVIES if t == title), "")
            row.title = row.title_auto = title_value if title_value is not None else title
            row.year = row.year_auto = year
            row.video_type = row.video_type_auto = kind
            row.status, row.label, row.disctype, row.devpath = status, label, disctype, device
            row.poster_url = row.poster_url_auto = poster_url if poster_url is not None else posters.get(title, "")
            row.imdb_id = row.imdb_id_auto = f"tt9{abs(hash(title)) % 1000000:06d}" if title in posters else ""
            row.start_time = now - datetime.timedelta(days=days, hours=2)
            row.stop_time = None if status not in ("success", "fail") else now - datetime.timedelta(days=days)
            row.job_length = "1:52:10" if row.stop_time else None
            row.hasnicetitle, row.ejected, row.errors = True, True, errors
            row.logfile, row.stage, row.no_of_titles = f"{label}.log", "", 6
            row.arm_version, row.crc_id, row.path = "2.24.4", f"{abs(hash(label)):016x}"[:16], path
            row.mountpoint, row.manual_start, row.manual_mode, row.is_iso = "", False, False, False
            db.session.add(row)
            db.session.commit()
            db.session.add(Config(dict(cfg.arm_config), row.job_id))
            with open(os.path.join(cfg.arm_config["LOGPATH"], row.logfile), "w", encoding="utf-8") as log:
                log.write(f"{row.start_time:%m-%d-%Y %H:%M:%S} ARM: INFO: Starting A2RM demo job for {label}\n")
            db.session.commit()
            return row

        # Older finished jobs for the library
        for days, title in enumerate(["Valley of Copper", "Copperhead Run", "Glass Garden", "The Quiet Mile",
                                      "Northbound", "Harbor Lights", "Paper Moons", "Signal Lost"], start=6):
            folder = f"{completed}movies/{title} ({next(y for t, y, _k, _c in MOVIES if t == title)})"
            name = os.path.basename(folder)
            video(f"{folder}/{name}.mkv", 5400 + days * 120, colours[title])
            row = job(title, "success", days, slug(title).upper().replace("-", "_"), path=folder,
                      disctype="dvd" if days % 2 else "bluray")
            store.update_job(row.job_id, confidence={"score": 88 + days % 10, "level": "high", "confirmed": False,
                                                     "reasons": ["The disc label matches the title",
                                                                 "The main title is within 2 minutes of TMDb's runtime"]},
                             encoder={"label": "QSV H.264 (GPU)", "gpu": True, "all_gpu": True, "fps": 980.0})
        failed = job("Copper Kettle Christmas", "fail", 3, "COPPER_KETTLE", disctype="dvd",
                     errors="MakeMKV couldn't read title 2 (read error at sector 1,204,881). Clean the disc or try the other drive.")
        store.update_job(failed.job_id, attention=[{"kind": "failed", "level": "bad",
                                                    "message": "MakeMKV couldn't read the disc: clean it or try the other drive."}])

        # The TV show: Season 2, four episodes
        tv = f"{completed}tv/Starfield Station (2015)"
        for number in range(1, 5):
            video(f"{tv}/Season 02/Starfield Station S02E0{number}.mkv", 2580 + number * 30, colours["Starfield Station"])
        row = job("Starfield Station", "success", 2, "STARFIELD_STATION_S2_D1", kind="series", path=tv, disctype="dvd",
                  device="/dev/sr0")
        store.update_job(row.job_id, tv_season={"season": 2, "first_episode": 1},
                         confidence={"score": 95, "level": "high", "confirmed": False,
                                     "reasons": ["The disc label names the show and the season"]},
                         episodes={"message": "Season 2, episodes 1-4"})

        # The Last Orchard: finished, but A2RM wasn't sure (banner on Home)
        orchard = f"{completed}movies/The Last Orchard (2021)"
        video(f"{orchard}/The Last Orchard (2021).mkv", 6420, colours["The Last Orchard"])
        row = job("The Last Orchard", "success", 1, "ORCHARD_D1", path=orchard)
        store.update_job(row.job_id, confidence={"score": 58, "level": "low", "confirmed": False,
                                                 "reasons": ["The disc label only partly matches the title",
                                                             "The main title is 9 minutes longer than TMDb's runtime"]},
                         suggestion={"title": "The Last Orchard", "year": "2021"},
                         attention=[{"kind": "confidence", "level": "warn",
                                     "message": "Not sure about The Last Orchard (2021): 58/100. Check the title."}])

        # Copper Valley: two cuts, stills, title analysis: the job page in the screenshots
        valley = f"{completed}movies/Copper Valley (2016)"
        cuts = [("Copper Valley (2016) - Theatrical.mkv", 6834, 0), ("Copper Valley (2016) - Extended cut.mkv", 7512, 1)]
        for name, seconds, _tid in cuts:
            video(f"{valley}/{name}", seconds, colours["Copper Valley"], label="COPPER VALLEY")
        for number, seconds in enumerate((312, 248, 1290), start=1):
            video(f"{valley}/extras/Copper Valley - Extra {number}.mkv", seconds, ("0x333333", "0x777777"), gb=seconds / 2000)
        row = job("Copper Valley", "success", 0, "COPPER_VALLEY_EXT", path=valley)
        titles = [
            {"tid": 0, "role": "main", "rip": True, "outcome": "ripped", "minutes": 113.9, "seconds": 6834,
             "segments": 14, "source": "00800.mpls", "language": "eng", "audio": ["eng", "eng", "fra"],
             "subtitles": ["eng", "fra"], "label": "Theatrical", "reason": "Closest to TMDb's runtime (114 min)"},
            {"tid": 1, "role": "cut", "rip": True, "outcome": "ripped", "minutes": 125.2, "seconds": 7512,
             "segments": 16, "source": "00801.mpls", "language": "eng", "audio": ["eng", "eng", "fra"],
             "subtitles": ["eng", "fra"], "label": "Extended cut (+11 min)", "reason": "Same film, 11 minutes longer"},
            {"tid": 2, "role": "extra", "rip": True, "outcome": "ripped", "minutes": 5.2, "seconds": 312,
             "segments": 1, "source": "00010.m2ts", "language": "eng", "audio": ["eng"], "subtitles": [],
             "label": "", "reason": "Shorter title (bonus material)"},
            {"tid": 3, "role": "duplicate", "rip": False, "outcome": "skipped", "minutes": 113.9, "seconds": 6834,
             "segments": 14, "source": "00802.mpls", "language": "eng", "audio": ["eng"], "subtitles": [],
             "label": "", "reason": "Exact copy of title 0"},
        ]
        store.update_job(row.job_id, titles={"titles": titles, "ambiguous_language": False},
                         confidence={"score": 96, "level": "high", "confirmed": False,
                                     "reasons": ["The disc label matches the title",
                                                 "The main title is within 1 minute of TMDb's runtime",
                                                 "TheDiscDB knows this disc"]},
                         files=[{"file": f"{valley}/{name}", "role": "main" if tid == 0 else "cut", "tid": tid}
                                for name, _s, tid in cuts]
                         + [{"file": f"{valley}/extras/Copper Valley - Extra {n}.mkv", "role": "extra", "tid": n + 1}
                            for n in (1, 2, 3)],
                         encoder={"label": "QSV H.265 (GPU)", "gpu": True, "all_gpu": True, "fps": 412.6})
        for tid, (name, seconds, _t) in enumerate(cuts):
            db.session.add(Track(row.job_id, str(tid), seconds, "16:9", 23.976, tid == 0, "MakeMKV",
                                 name, name, chapters=24, filesize=24_000_000_000 + tid * 2_000_000_000))
        db.session.commit()

        # A disc A2RM couldn't identify: filed under unidentified/ with stills
        unknown = job("", "success", 4, "DISC_0417", disctype="dvd", device="/dev/sr0", title_value="DISC_0417",
                      year="", poster_url="", kind="unknown")
        unidentified = f"{completed}unidentified/DISC_0417 (job {unknown.job_id})"
        video(f"{unidentified}/DISC_0417.mkv", 5820, ("0x444444", "0x999999"))
        unknown.path = unidentified
        db.session.commit()
        store.update_job(unknown.job_id, confidence={"score": 12, "level": "low", "confirmed": False,
                                                     "reasons": ["The disc label is just a number"]})

        # Now ripping on the Blu-ray drive
        active = job("Starfield Station", "ripping", 0, "STARFIELD_STATION_S2_D2", kind="series", device="/dev/sr1")
        active.stage, active.stop_time = "Ripping title 2 of 4", None
        with open(f"{cfg.arm_config['LOGPATH']}progress/{active.job_id}.log", "w", encoding="utf-8") as progress:
            progress.write("PRGV:30147,30147,65536\n")  # MakeMKV progress: 46%
        drives[1].job_id_current = active.job_id
        db.session.commit()

    # Library > Files: things that need tidying, made up as well
    video(f"{completed}movies/Harbor Lights (2011).mkv", 5400 + 11 * 120, colours["Harbor Lights"])  # loose copy
    paper = 5400 + 12 * 120  # Paper Moons was job 7 (days=12): an ARM copy folder with the same file
    video(f"{completed}movies/Paper Moons (2009)_179445443439/Paper Moons (2009).mkv", paper, colours["Paper Moons"])
    open(f"{completed}movies/The Quiet Mile (2015)/sample.mkv", "w").close()  # an empty file
    video(f"{completed}movies/Northbound/Northbound.mkv", 6060, colours["Northbound"])             # no year
    video(f"{completed}movies/Glass Garden (2020)/B1_t03.mkv", 6000, colours["Glass Garden"])      # MakeMKV name


# ---------------------------------------------------------------- outside services replaced with demo values

SEARCH = [("Copper Valley", "2016", "movie"), ("Valley of Copper", "1987", "movie"), ("Copperhead Run", "2013", "movie"),
          ("Copper Kettle Christmas", "2019", "movie"), ("Copper Valley Chronicles", "2022", "series"),
          ("The Copper Road", "2004", "movie")]


def patch():
    from arm.ui import utils as ui_utils
    from arm.ui.settings import settings as settings_routes
    from arm.automation import hwreport, hwcheck, tmdb

    for title, year, _kind in SEARCH:
        if title not in [m[0] for m in MOVIES]:
            poster(title, year, ("0x343a40", "0xadb5bd"))

    def details(title):
        year, kind = next(((y, k) for t, y, k in SEARCH if t == title), ("", "movie"))
        return {"Title": title, "Year": year, "Type": kind, "imdbID": f"tt9{abs(hash(title)) % 1000000:06d}",
                "Poster": f"{DEMO_URL}/static/demo/posters/{slug(title)}.png",
                "Plot": f"{title} is a made-up film for the A2RM screenshots.", "background_url": None,
                "Details": "Made up for the demo"}

    def metadata_selector(func, query="", year="", imdb_id="", media_type=None):
        if func == "search":
            words = set(str(query).lower().split())
            hits = [details(t) for t, _y, k in SEARCH if words & set(t.lower().split())
                    and (not media_type or media_type == "both" or media_type == k)]
            return {"Search": hits} if hits else {"Error": "not found"}
        title = next((t for t, _y, _k in SEARCH if f"tt9{abs(hash(t)) % 1000000:06d}" == imdb_id), query)
        return details(title) if title else None

    ui_utils.metadata_selector = metadata_selector
    tmdb.season_choices = lambda imdb_id: [{"number": 1, "name": "Season 1", "year": "2022", "episodes": 8}]
    # A made-up Intel GPU (the same shapes hwreport builds from /sys, /dev/dri and vainfo)
    hwreport.gpus = lambda: [{"card": "card0", "vendor": "Intel", "pci_id": "8086:a780", "driver": "i915"}]
    hwreport.render_nodes = lambda: [{"path": "/dev/dri/renderD128", "gid": 993, "usable": True, "in_group": True,
                                      "fix": ""}]
    hwreport.vainfo = lambda node: {"ok": True, "driver": "Intel iHD driver for Intel(R) Gen Graphics - 24.3.4",
                                    "encode_profiles": ["H264Main", "H264High", "HEVCMain", "HEVCMain10"],
                                    "codecs": {"H.264": True, "HEVC": True, "AV1": False}, "error": ""}
    real_handbrake = hwreport.handbrake_info

    def handbrake_info():
        info = real_handbrake()  # the HandBrake in the image; QSV encoders only list with a GPU present
        info["encoders"] = info["encoders"] + ["qsv_h264", "qsv_h265", "qsv_h265_10bit"]
        return info
    hwreport.handbrake_info = handbrake_info
    hwcheck.last_result = lambda encoder: {"encoder": encoder, "ok": True, "seconds": 0.9, "cause": "", "fix": "",
                                           "log": "", "checked": datetime.datetime.now().timestamp() - 3600}

    class Stats:
        cpu_util, cpu_temp, memory_percent = 6.0, 41.0, 23.0
        storage_completed_free, storage_completed_percent, storage_transcode_free = 2384.6, 41, 2384.6

    import arm.ui.settings.ServerUtil as server_util
    server_util.ServerUtil = Stats
    settings_routes.check_hw_transcode_support = lambda: {"intel": True}


if __name__ == "__main__":
    write_config()
    sys.path.insert(0, "/opt/arm")
    seed()
    patch()
    from arm.automation import health
    health.run()
    from arm.ui import app
    import arm.ui.routes  # noqa: F401  the blueprints, as runui does
    print(f"A2RM demo on port {PORT} ({DEMO_URL})", flush=True)
    app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)
