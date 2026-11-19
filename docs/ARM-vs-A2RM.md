# ARM and A2RM: what's different

A2RM is a fork of the [Automatic Ripping Machine (ARM)](https://github.com/automatic-ripping-machine/automatic-ripping-machine),
based on ARM **2.24.4**. This page lists what A2RM changes, what it keeps, and what to expect if you move between the
two. The [README](../README.md) is the short version.

## In one paragraph

ARM rips a disc when you put it in: it reads the label, looks the title up, runs MakeMKV and HandBrake, and files
the result. What's left to you is checking that it picked the right film, choosing which titles to keep, fixing
names, and tidying up afterwards. A2RM keeps ARM's whole ripping pipeline and adds the decisions around it: how sure
it is about the title, which titles on the disc matter, what to call the files, checks that the files are complete,
and the cleanup. It asks you only when it needs to, by phone notification or a banner in the web UI.

## What stays the same

| Area | Same as ARM |
|---|---|
| Ripping | MakeMKV for DVD/Blu-ray, HandBrake (or FFmpeg) for transcoding, abcde for CDs, `dd` for data discs |
| Configuration | `arm.yaml`, `apprise.yaml`, `abcde.conf` in `/etc/arm/config`; every ARM setting still works |
| Database | ARM's SQLite database, **with no schema changes**. A2RM's own records live next to it (JSON files in `<DB folder>/automation/`) |
| Docker | Same base image (`automaticrippingmachine/arm-dependencies`), same volumes, ports, users and udev trigger |
| Folders | `raw/`, `transcode/`, `completed/movies`, `completed/tv`, `completed/unidentified`, music folder |
| Metadata | OMDb or TMDb (`METADATA_PROVIDER`), ARM's disc-ID (CRC64) database |
| Login | Same users and password handling; default `admin` / `password` |

Moving back to ARM: point an ARM 2.24.4 image at the same volumes. ARM ignores A2RM's extra settings and its
`automation/` folder. Files A2RM renamed stay renamed.

## Identification

| | ARM | A2RM |
|---|---|---|
| How it picks a title | Disc-ID database, then a search on the cleaned-up label; first result wins | Same sources, plus a **confidence score (0-100)** with reasons: label match, main title length vs TMDb runtime, earlier rips of the same disc |
| Vague labels (`DISC_1`, `MOVIE`) | First search result | Searches TMDb and picks the result whose **runtime matches** the disc's main title (`RUNTIME_MATCHING`) |
| A wrong disc-ID database entry | Trusted | Checked against the label and runtime; a better match replaces it, and a low score suggests an alternative (e.g. "Doctor Who (2005), TV show" instead of King Kong Escapes) |
| Title spelling | From the label: `World-War-Z` | TMDb's spelling: `World War Z` |
| Manual wait | Optional, the same length for every disc (`MANUAL_WAIT`, `MANUAL_WAIT_TIME`) | No wait above `CONFIDENCE_SKIP_WAIT_ABOVE` (90); a longer wait and a phone notification below `CONFIDENCE_NOTIFY_BELOW` (70); "Start now" ends the wait |
| Nobody answers | Filed under the guess | Ripped as usual but filed under `unidentified/<label> (job N)/` with six stills, until you name it (`LOW_CONFIDENCE_ACTION`) |
| Discs seen before | Ripped again | Recognised by fingerprint; rip again, rip and tell you, or skip and eject (`ALREADY_RIPPED_ACTION`) |
| TheDiscDB | Not used | Optional (`DISCDB_LOOKUP`, off): exact title, main feature, extras and TV episode numbers for known discs. Only a 32-character hash of the file sizes is sent |
| Title-card OCR | Not used | Optional (`TITLE_OCR`, off): Tesseract reads the opening minutes on your server and suggests a TMDb match |
| TV shows | Movie or TV comes from the first search result | Movie/TV switch on the job, search results that tell shows apart (years, seasons, network), season and first-episode choice |

## Which titles get ripped

ARM rips every title over `MINLENGTH` (or only the main feature with `MAINFEATURE: true`). On many discs that means
copy-protection decoys, "play all" titles and language duplicates.

A2RM reads MakeMKV's title information first (`TITLE_ANALYSIS`) and classes each title:

| Class | What happens |
|---|---|
| Main feature | Ripped, named `Movie (Year).mkv` |
| Other cut (theatrical / extended) | Ripped as a Jellyfin version: `Movie (Year) - Extended cut (+11 min).mkv` (`KEEP_ALTERNATE_CUTS`) |
| Language version | Only your `PREFERRED_LANGUAGE` is kept, unless `KEEP_OTHER_LANGUAGE_VERSIONS`. If the versions can't be told apart, all are kept and the job page shows stills so you can pick one |
| Duplicate (copy protection) | Skipped (`SKIP_DUPLICATE_TITLES`) |
| "Play all" | Skipped. Found by length: 2+ other titles add up to it |
| Extra | Ripped into `extras/` with a readable name (`ORGANIZE_EXTRAS`) |
| TV episode | Named `Show S01E03.mkv` in `Season 01/`, matched from TMDb's episode runtimes or TheDiscDB (`TV_EPISODE_MATCHING`) |

## Names and folders

| | ARM | A2RM |
|---|---|---|
| Movie file | MakeMKV's names (`title_t00.mkv`) or the title | `{Title} ({Year})`, configurable (`MOVIE_NAME_PATTERN`) |
| Episodes | MakeMKV's names | `{Show} S{Season:00}E{Episode:00}` in `Season {Season:00}` (`EPISODE_NAME_PATTERN`, `SEASON_FOLDER_PATTERN`) |
| Second rip of the same title | A new folder with a number (`Movie (2010)_1791...`) | Already-ripped check first; the library page flags existing copy folders |
| Jellyfin match | Jellyfin guesses from the folder name | A minimal `movie.nfo` / `tvshow.nfo` with the TMDb and IMDb ids (`WRITE_NFO`, on). NFOs from other tools are never overwritten |
| Changing a title later | Updates the database only | Moves and renames the files to match (including `unidentified` → `movies`/`tv`) |

## Checks and safety nets (all A2RM)

- **Disk space** before ripping (`MIN_FREE_GB_DVD`, `MIN_FREE_GB_BLURAY`, `DISK_SPACE_ACTION`).
- **Finished files checked** against the disc title's length before the raw rip is deleted (`VERIFY_OUTPUT`).
- **Unfinalized MakeMKV files** (an interrupted rip) caught and retried (`CHECK_UNFINALIZED`);
  `scripts/a2rm-repair-mkv.py` repairs existing ones without touching the original.
- **MakeMKV read errors** retried with a hint (`READ_ERROR_RETRIES`); an expired MakeMKV gives a clear message.
- **Stuck jobs** (no progress for `STUCK_JOB_MINUTES`) stopped and the disc ejected.
- **Hardware encoder tested before ripping** (`HW_ENCODER_CHECK`): ARM trusts HandBrake's "qsv is available", which is
  printed even without a GPU, so a broken QuickSync setup fails the job after the whole rip. A2RM runs a 3-second test
  encode and, if it fails, transcodes on the CPU instead (`HW_ENCODER_FALLBACK`).
- **Database locks** at the end of the manual wait are retried instead of killing the job.

## Housekeeping (all A2RM)

Runs from one scheduled command (`python3 -m arm.automation.maintenance tick`, e.g. every 5 minutes from the host's cron):

- Daily database backup (`DB_BACKUP_*`).
- Cleanup of old raw/transcode leftovers and of failed jobs that were later ripped successfully (`CLEANUP_*`).
- Daily summary notification (`DAILY_SUMMARY_*`).
- Weekly **library health check** (`HEALTH_CHECK_*`): empty, unfinalized or unreadable files, files without video,
  movies far from TMDb's runtime, probable duplicates. Read-only.
- **Jellyfin**: scans the new folder right away and checks it matched the same film or show (`JELLYFIN_*`).

## Library management (all A2RM)

**Library > Manage files** works on everything in `movies/`, `tv/` and `unidentified/`, including files A2RM (or
ARM) didn't make. It flags ARM's numbered copy folders, loose files, MakeMKV names, season-only folders, missing years
and the same title in more than one folder. Per item you can set the type, title, year, IMDb id, season and the role
of each file, preview every move, apply, and undo. Merges never overwrite anything. Removed items go to a trash folder
that is only emptied when you confirm.

## Notifications

ARM sends notifications through Apprise and its built-in services (Pushbullet, Pushover, etc.); they are messages
only. A2RM keeps those and adds [ntfy](https://ntfy.sh) phone notifications **with buttons**: "Yes, that's it",
"Use the suggestion", "Start now", "Pick the title". The buttons are signed links back to the web UI
(`ARM_PHONE_URL`), so they work without logging in but can't be forged.

## Web UI

| ARM | A2RM |
|---|---|
| Home: a card per active job | Home: a "bay" per drive (empty, identifying, waiting for you, ripping) with steps, progress and time left; a "needs you" strip with buttons; recent rips |
| Job history table | Poster **Library** with filters (movies, TV, music, unidentified, failed) and search; the history table is still there |
| Job details: ARM's fields | Also: confidence and reasons, titles on the disc and why each was kept or skipped, stills, language versions, encoder used, season choice, movie/TV switch |
| Settings: ARM's tabs | Also **Settings > Automation** (every A2RM option, grouped, with help text), a **Hardware check** page and a **Library health** page |
| Desktop layout | Works on phones (bottom tab bar), light and dark themes |

## Docker image

| | ARM image | A2RM `Dockerfile` |
|---|---|---|
| MakeMKV | The version baked into `arm-dependencies` (expires) | The current version from makemkv.com at build time (`MAKEMKV_VERSION`); `scripts/update-makemkv.sh` rebuilds when it expires |
| Intel QuickSync | No Intel media driver in the image; setting it up is manual and usually needs `--privileged` | Optional `--build-arg INTEL_QSV=1` (Intel driver, oneVPL, HandBrake built against them); works without `--privileged`. See [quicksync.md](quicksync.md) |
| Checks | None | `a2rm-check` in the image (MakeMKV expiry, a real QuickSync test encode); `scripts/a2rm-host-check.sh` on the host |
| OCR | No | Tesseract (for `TITLE_OCR`) |
| Prebuilt image | Docker Hub | None: build it yourself (about 2 minutes, or 10 with QuickSync) |

## Fixes to ARM itself

A2RM also fixes bugs in ARM 2.24.4. Most were offered to ARM as pull requests
([#1893](https://github.com/automatic-ripping-machine/automatic-ripping-machine/pull/1893) to
[#1907](https://github.com/automatic-ripping-machine/automatic-ripping-machine/pull/1907)):

- MakeMKV kept running after a job was abandoned or ended early.
- Wrong DVD titles from the label being searched as loose words.
- A "fatal error" notification on every eject; drives left "busy" after a crashed job.
- The 99-title check skipped when libdvdread printed a warning.
- MakeMKV failures with no reason given; disc labels missing when udev hadn't read them.
- Title, year and type typed in the UI overwritten during identification.
- `arm.yaml` rewritten on every start (and `"None"` values written into it).
- Abandon signalling the wrong process; nine small crashes in the UI and ripper.
- The ntfy password, MakeMKV key and `JSON_URL` printed in logs.
- MusicBrainz and data-disc edge cases; unlabelled Blu-rays; a disc-ID lookup with no timeout.
- Phantom drives shown when no drive is passed into the container.

## Outside services

| Service | ARM | A2RM |
|---|---|---|
| OMDb / TMDb | Title lookup | Same, plus runtimes, TV seasons and episode lengths from TMDb |
| ARM disc-ID database (`JSON_URL`) | Optional | Same |
| TheDiscDB | No | Only with `DISCDB_LOOKUP` (sends a hash of file sizes) |
| ntfy | No | Only with `NTFY_SERVER` (your own server or ntfy.sh) |
| Jellyfin, tinyMediaManager | No | Only with `JELLYFIN_URL` / `TMM_URL` (your own servers) |
| makemkv.com | Not at build time | At build time, to get the current MakeMKV |

Nothing else leaves your network. Title-card OCR runs locally.

## Every A2RM setting

All in `arm.yaml`, added on first start, and on **Settings > Automation** in the web UI. Defaults in brackets.

- **Phone notifications:** `NTFY_SERVER` (""), `NTFY_TOPIC` (""), `NTFY_TOKEN` (""), `NTFY_SEND_ALL` (off),
  `ARM_PHONE_URL` ("")
- **Identification:** `CONFIDENCE_ENABLED` (on), `CONFIDENCE_NOTIFY_BELOW` (70), `CONFIDENCE_SKIP_WAIT_ABOVE` (90),
  `LOW_CONFIDENCE_WAIT_TIME` (900 s), `LOW_CONFIDENCE_ACTION` (unidentified), `START_NOW_BUTTONS` (on),
  `DISCDB_LOOKUP` (off), `DISCDB_URL`, `TITLE_OCR` (off), `RUNTIME_MATCHING` (on)
- **Before ripping:** `ALREADY_RIPPED_ACTION` (notify), `MIN_FREE_GB_DVD` (20), `MIN_FREE_GB_BLURAY` (80),
  `DISK_SPACE_ACTION` (wait)
- **Titles:** `TITLE_ANALYSIS` (on), `KEEP_ALTERNATE_CUTS` (on), `SKIP_DUPLICATE_TITLES` (on), `ORGANIZE_EXTRAS` (on),
  `MOVIE_NAME_PATTERN`, `EPISODE_NAME_PATTERN`, `SEASON_FOLDER_PATTERN`, `TV_EPISODE_MATCHING` (on),
  `TV_SEASON_CHOICE` (on)
- **Languages:** `PREFERRED_LANGUAGE` (eng), `KEEP_OTHER_LANGUAGE_VERSIONS` (off), `TRIM_TRACKS` (off),
  `KEEP_AUDIO_LANGUAGES` (eng), `KEEP_SUBTITLE_LANGUAGES` (eng)
- **Checks:** `VERIFY_OUTPUT` (on), `VERIFY_TOLERANCE_SECONDS` (90), `READ_ERROR_RETRIES` (1), `CHECK_UNFINALIZED` (on),
  `STUCK_JOB_MINUTES` (30), `STUCK_JOB_ACTION` (abandon)
- **Hardware encoding:** `HW_ENCODER_CHECK` (on), `HW_ENCODER_FALLBACK` (on), `RECORD_ENCODER` (on),
  `HW_DECODE_BLURAY` (off)
- **Jellyfin:** `JELLYFIN_URL`, `JELLYFIN_API_KEY`, `JELLYFIN_MEDIA_ROOT` (/media), `JELLYFIN_FOLDER_MAP`,
  `JELLYFIN_VERIFY_MATCH` (on), `WRITE_NFO` (on), `TMM_URL`, `TMM_API_KEY`
- **Housekeeping:** `DB_BACKUP_ENABLED` (on), `DB_BACKUP_TIME` (03:00), `DB_BACKUP_KEEP` (14), `DB_BACKUP_DIR`,
  `CLEANUP_ENABLED` (on), `CLEANUP_LEFTOVER_DAYS` (3), `CLEANUP_FAILED_DUPLICATE_JOBS` (on),
  `DAILY_SUMMARY_ENABLED` (on), `DAILY_SUMMARY_TIME` (08:00), `HEALTH_CHECK_DAYS` (7), `HEALTH_CHECK_TIME` (04:00),
  `HEALTH_CHECK_DECODE` (off)

## Support

A2RM is a personal fork. It isn't affiliated with or supported by the ARM project: please don't report A2RM problems
to ARM's GitHub or Discord. ARM's [wiki](https://github.com/automatic-ripping-machine/automatic-ripping-machine/wiki)
is still the best guide to the parts A2RM didn't change (drives, Docker volumes, permissions, MakeMKV keys); a copy is
in [`arm_wiki/`](../arm_wiki).
