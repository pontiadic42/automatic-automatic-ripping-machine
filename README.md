# A2RM: Automatic Automatic Ripping Machine

**A2RM** (say "A-squared-RM") is a fork of the
[Automatic Ripping Machine (ARM)](https://github.com/automatic-ripping-machine/automatic-ripping-machine).
ARM already rips a disc when you put it in. A2RM tries to take the remaining manual steps out: working out what the
disc is, which titles on it to keep, what to name the files, and cleaning up afterwards. The idea is that you put a
disc in and only hear about it when it really needs you.

A2RM is based on ARM 2.24.4. All the credit for ARM itself goes to its authors and contributors; see
[Credits](#credits). A2RM is a personal fork and isn't affiliated with or supported by the ARM project, so please
don't report A2RM problems to them.

**[ARM and A2RM: what's different](docs/ARM-vs-A2RM.md)** compares the two area by area (identification, which
titles get ripped, names, checks, housekeeping, web UI, Docker image, bug fixes, outside services) and lists every
A2RM setting with its default.

## What A2RM adds to ARM

### Identification
- **Confidence score (0-100)** for every disc, with plain-language reasons: how well the disc label matches the
  title, whether the main title's length matches TMDb's runtime, and whether the same disc was ripped before.
- **No waiting when it's sure.** At or above a score you choose (default 90) the rip starts right away instead of
  sitting through the manual-wait period. Below another score (default 70) A2RM waits longer and asks you.
- **Runtime matching.** When a label is vague, A2RM searches TMDb and picks the result whose runtime matches the
  disc's main title.
- **Remembers discs.** A disc without a label that was identified before (same DVD fingerprint) gets its earlier
  title automatically.
- **Phone notifications through [ntfy](https://ntfy.sh)** with "Yes, that's it" and "Pick the title" buttons.
- **"Start now"** (home page, job page, phone) ends the manual wait and rips with the current title.
- **TV shows:** search results say which show is which (years, seasons, network, country); you can pick the season
  and the first episode on the disc, and switch a job between movie and TV show.
- **Nobody answered?** A disc A2RM isn't sure about is still ripped, but filed under `unidentified/<label> (job N)/`
  with six stills to help you recognise it; accepting or picking the title later moves it to movies/TV
  (`LOW_CONFIDENCE_ACTION`).
- **[TheDiscDB](https://thediscdb.com) lookup** (`DISCDB_LOOKUP`, off by default because it contacts an outside
  service): A2RM computes the disc's TheDiscDB hash from its file sizes and sends only that 32-character hash. A known
  disc gets its exact title, a top confidence score, the right main title and extras, and the TV episode numbers.
- **Title-card OCR** (`TITLE_OCR`, off by default): for a disc A2RM isn't sure about, Tesseract reads the text on
  frames from the first five minutes (on your server) and a TMDb match with the right runtime becomes the suggestion.
  Works for titles in plain type, not stylised logos.

### Ripping the right titles
- **Title analysis** from MakeMKV's title information (segment maps, languages, audio and subtitle streams). Each
  title is classed as the main feature, another cut (theatrical / extended), a language version, a duplicate (copy
  protection), a "play all" title, an extra, or a TV episode.
- Only what's needed is ripped. Duplicates and other-language versions are skipped (configurable). When language
  versions can't be told apart, all are kept and the job page shows still frames from each.
- **"Play all" titles** on TV discs are found by their length (2+ other titles add up to them), so DVD episodes
  aren't mistaken for them.
- **Jellyfin-friendly names:** `Movie (Year).mkv`, `Movie (Year) - Extended cut (+11 min).mkv` (Jellyfin offers it as a
  version), readable names in `extras/`, and `Show/Season 01/Show S01E03.mkv` matched from TMDb's episode runtimes
  (or TheDiscDB's numbers). Titles keep TMDb's spelling (`World War Z`, not `World-War-Z`).
- **Your own naming pattern** (`MOVIE_NAME_PATTERN`, `EPISODE_NAME_PATTERN`, `SEASON_FOLDER_PATTERN`), Radarr/Sonarr
  style: `{Title}`, `{Year}`, `{Edition}`, `{ImdbId}`, `{Show}`, `{Season:00}`, `{Episode:00}`, `{EpisodeTitle}`.
  Settings shows an example while you type. The folder itself stays `Title (Year)`.
- **`.nfo` files with the TMDb id** (`WRITE_NFO`, on): a minimal `movie.nfo` / `tvshow.nfo` so Jellyfin matches
  exactly the film A2RM ripped. NFOs written by other tools are never overwritten. Optionally asks tinyMediaManager
  (`TMM_URL`, `TMM_API_KEY`) to scrape the new item.

### Checks and safety nets
- **Already ripped?** Rip again, rip and tell you, or skip and eject.
- **Disk space** is checked before ripping.
- **Finished files are checked** against their disc title's length before the raw rip is deleted; a file that looks
  cut short keeps its raw files and you're told.
- **MakeMKV retries** after a read failure, with a "clean the disc or try the other drive" hint, and a clear message
  when MakeMKV itself has expired.
- **Stuck-job watchdog:** a rip with no progress for a while is stopped, the disc ejected, and you're told.
- **Correcting a title later moves the files** to the new name (and from `unidentified` to `movies` if needed).
- **Unfinalized MakeMKV files** (an interrupted rip leaves files nothing can open) are caught right after the rip
  (`CHECK_UNFINALIZED`) and the rip is retried; `scripts/a2rm-repair-mkv.py` repairs ones you already have.
- **Hardware encoder test before ripping** (`HW_ENCODER_CHECK`): a 3-second real test encode, because HandBrake's
  "qsv is available" can't be trusted. If the GPU encoder fails, A2RM transcodes on the CPU instead of failing the job
  after the rip (`HW_ENCODER_FALLBACK`). The job page shows which encoder each job actually used.
- **Database locks** at the end of the manual wait no longer kill the job (it retries), and drives that aren't passed
  into the container aren't shown.
- Optional: keep only your audio/subtitle languages when transcoding (Settings > A2RM > **Languages**, with
  drop-downs).

### Housekeeping
- Daily database backup, cleanup of leftover raw/transcode folders and of failed jobs that were later ripped
  successfully, and a daily summary notification.
- **Library health check** (every `HEALTH_CHECK_DAYS`, default 7; never while ripping or transcoding): empty or
  unfinalized files, files ffprobe can't read or without video, movies far from TMDb's runtime, probable duplicates.
  Read-only; results on Library > Health and in the daily summary.
- **Library > Files** (and each job's page): everything in `movies/`, `tv/` and `unidentified/`, including files A2RM didn't make.
  Flags ARM's `_<number>` copy folders, loose files, MakeMKV names never renamed, season-only show folders, missing
  years and the same title in several folders. Per item: set type, title, year, IMDb id (TMDb search), season and
  first episode, and a role per file; preview every move, then apply. Merges never overwrite (same-size files stay
  put, others get " (2)"); jobs, `.nfo` and Jellyfin are updated. Move to trash, empty trash (asks first), undo.
- **Jellyfin:** scans the new folder right away, then checks it matched the same movie or show.

### Web UI
- New layout: a home page with one "bay" per drive (empty / identifying / waiting for you / ripping, with steps,
  progress and time left), a "needs you" strip with buttons (accept title, pick another, cancel job, delete files,
  dismiss), and recently finished rips.
- A poster **Library** with filters (movies, TV, music, unidentified, failed) and search.
- Works on phones (bottom tab bar, list layouts), light and dark themes.
- **One page per job:** banners and buttons at the top, then Identify (TMDb search with large posters, pick the
  season, or type the title), Files (language versions, rename and move), Disc (titles, stills, encoder) and Details
  (ARM's record, tracks, rip settings).
- **Library** tabs: Titles, Files, Health, History. **Settings** tabs: A2RM (every A2RM option), Ripping, Hardware
  (GPU check, test encode, encoder suggestions, hardware-encoding settings), Notifications (ntfy and Apprise), Music,
  Interface, System (server, drives, database), Help.

### Fixes to ARM itself
A2RM also carries a set of bug fixes to ARM 2.24.4: wrong titles from disc labels, crashes in identification,
notifications for empty drives, stale drive locks, abandoned jobs that kept MakeMKV running, config file rewrites,
secrets in logs, database locks while ripping, double runs for one disc, slow drive spin-up, and more. Most of these
were also offered to ARM as pull requests.

## Configuration

Everything A2RM adds is a normal setting in `arm.yaml` (added automatically on first start) and can be changed on
**Settings > A2RM** in the web UI (phone notifications under Settings > Notifications, hardware encoding under
Settings > Hardware). Each feature can be switched off. Things you'll want to set:

| Setting | What it's for |
|---|---|
| `NTFY_SERVER`, `NTFY_TOPIC` (+ `NTFY_TOKEN`) | Phone notifications. Empty = off. |
| `ARM_PHONE_URL` | How your phone reaches A2RM (for notification buttons), e.g. `http://192.168.1.50:8080`. |
| `JELLYFIN_URL`, `JELLYFIN_API_KEY` | Jellyfin scans and match checks. Empty = off. |
| `CONFIDENCE_SKIP_WAIT_ABOVE`, `CONFIDENCE_NOTIFY_BELOW` | When to start right away / when to ask you. |
| `ALREADY_RIPPED_ACTION` | `rip`, `notify` or `skip` for discs you already have. |
| `PREFERRED_LANGUAGE` | Picks the main version on multi-language discs (ISO 639-2, e.g. `eng`). |
| `LOW_CONFIDENCE_ACTION` | `unidentified` (default) files unsure discs under `unidentified/` until you name them; `guess` files them under the guess. |
| `DISCDB_LOOKUP`, `TITLE_OCR` | Extra identification (both off by default; see above). |
| `MOVIE_NAME_PATTERN`, `EPISODE_NAME_PATTERN` | File names. |
| `WRITE_NFO`, `TMM_URL` | `.nfo` files for Jellyfin; tinyMediaManager scrape. |
| `HEALTH_CHECK_DAYS` | Library health check interval (0 = off). |

The housekeeping tasks (watchdog, backups, cleanup, summary, Jellyfin check, library health check) run from a small
scheduled command, for example every 5 minutes from the Docker host's cron:

```bash
docker exec -u arm -w /opt/arm -e ARM_LOCAL_TZ=$(timedatectl show -p Timezone --value) arm-rippers python3 -m arm.automation.maintenance tick
```

## Install

A2RM runs in Docker, like ARM. There is no prebuilt A2RM image: you build it from this repo (about 2 minutes, or
about 10 with QuickSync). The `Dockerfile` is ARM's plus A2RM's additions (current MakeMKV, optional QuickSync,
Tesseract for title-card OCR). You don't need the `arm-dependencies` git submodule: the base image
`automaticrippingmachine/arm-dependencies` comes from Docker Hub.

> **Don't use ARM's installers or its Docker Hub image** (`automaticrippingmachine/automatic-ripping-machine`), which
> ARM's wiki points to: they install plain ARM, not A2RM. ARM's install scripts were removed from this repo for that
> reason.

ARM's [wiki](https://github.com/automatic-ripping-machine/automatic-ripping-machine/wiki) and
[Docker guide](https://github.com/automatic-ripping-machine/automatic-ripping-machine/wiki/docker) are still the best
guide to the parts A2RM doesn't change: the `arm` user on the host, drive permissions, volumes, MakeMKV keys. A copy
is in [`arm_wiki/`](arm_wiki).

1. **Build the image:**

   ```bash
   git clone https://github.com/pontiadic42/automatic-automatic-ripping-machine.git a2rm
   cd a2rm
   docker build -t a2rm:latest .                                # current MakeMKV, CPU encoding
   docker build --build-arg INTEL_QSV=1 -t a2rm:latest .        # or: plus Intel QuickSync (~10 min longer)
   ```

2. **Create the container.** Replace the `<...>` parts. Pass in each optical drive you have:

   ```bash
   docker run -d --name arm-rippers --restart always \
       -p 8080:8080 \
       -e ARM_UID="$(id -u arm)" -e ARM_GID="$(id -g arm)" \
       -e TZ="$(timedatectl show -p Timezone --value)" \
       -v <arm home>:/home/arm \
       -v <music folder>:/home/arm/music \
       -v <logs folder>:/home/arm/logs \
       -v <media folder>:/home/arm/media \
       -v <config folder>:/etc/arm/config \
       --device /dev/sr0:/dev/sr0 \
       --privileged \
       a2rm:latest
   ```

   `--privileged` is what ARM uses so the container can see discs being inserted. For QuickSync add
   `--device /dev/dri/renderD128` (run `scripts/a2rm-host-check.sh` on the host for your exact lines).

3. **Open the web UI** at `http://<server>:8080`. The default login is **admin / password** (as in ARM;
   `DISABLE_LOGIN: false`); change it after the first start. Like ARM, the home page (drives and recent rips) can be
   seen without logging in.

4. **Set A2RM up** under **Settings**: at least your phone notifications on the Notifications tab (`NTFY_SERVER`, `NTFY_TOPIC`,
   `ARM_PHONE_URL`) and, if you use it, Jellyfin. Every A2RM setting is described there and in
   [docs/ARM-vs-A2RM.md](docs/ARM-vs-A2RM.md#every-a2rm-setting).

5. **Schedule the housekeeping** command shown under [Configuration](#configuration) (every 5 minutes from the
   host's cron).

6. **Check the setup:** `docker exec arm-rippers a2rm-check`.

Coming from ARM? Point the new container at your existing volumes. The database and `arm.yaml` are used as they are
(A2RM adds its settings to `arm.yaml` on first start and doesn't change the database schema), so you can go back to
ARM the same way.

| Build option | What it does |
|---|---|
| `MAKEMKV_VERSION` | `latest` (default, looked up on makemkv.com) or a version such as `2.0.0` |
| `INTEL_QSV=1` | Intel media driver + oneVPL runtime, and HandBrake compiled against them. See [docs/quicksync.md](docs/quicksync.md) |
| `HANDBRAKE_VERSION` | HandBrake version built with `INTEL_QSV=1` (default 1.11.2) |

Some things to know:
- **MakeMKV** isn't part of A2RM; the build downloads it from makemkv.com. The free beta stops working when a newer
  MakeMKV comes out ("This application version is too old"), so rebuild then (or buy a key):
  `scripts/update-makemkv.sh a2rm:latest` finds the current version and rebuilds the image (add
  `--build-arg INTEL_QSV=1` if you use QuickSync), then recreate the container.
- **Check the setup** any time with `docker exec <container> a2rm-check`: MakeMKV version and whether it has expired,
  and whether QuickSync really works (a real test encode as ARM's user).
- **QuickSync:** `scripts/a2rm-host-check.sh` on the host prints the `--device` / `--group-add` lines you need; the full
  guide is [docs/quicksync.md](docs/quicksync.md).
- Keep the database, logs and MakeMKV settings on host volumes so a container rebuild doesn't lose them.

## Tests

Run the tests inside the A2RM/ARM image (they need its tools and Python packages). The image doesn't include
pytest, and the tests need ARM's config files in place:

```bash
pip install pytest
mkdir -p /etc/arm/config
cp setup/arm.yaml /etc/arm/config/arm.yaml
cp setup/.abcde.conf /etc/arm/config/abcde.conf     # imports fail without it
python3 -m pytest test/unittest
```

The `test_automation_*` files cover the A2RM features. Expected result: everything passes except 6 of ARM's own
tests (4 in `test_ripper_ARMInfo.py` that need a git checkout, 2 in `test_ripper_processhandler.py`).

## Support

A2RM is a personal project: there is no issue tracker and no Discord. ARM's GitHub, wiki and Discord are for ARM;
please don't take A2RM questions there. The web UI's Help tab and footer link to this repository.
What changed in each A2RM build is in [CHANGELOG.md](CHANGELOG.md).

## Credits

A2RM is built on the [Automatic Ripping Machine](https://github.com/automatic-ripping-machine/automatic-ripping-machine),
created by Benjamin Bryan and maintained by Andrew Sneed and many contributors. Thank you for ARM.
The original write-up: https://b3n.org/automatic-ripping-machine

A2RM changes were written with the help of [Claude Code](https://claude.com/claude-code).

## License

[MIT License](LICENSE), the same as ARM. The original copyright notices are kept.
