# Changelog

## A2RM

A2RM has no version numbers of its own yet; changes are listed by date. The Docker image still reports ARM's version
(`VERSION`, 2.24.4). See [docs/ARM-vs-A2RM.md](docs/ARM-vs-A2RM.md) for the full picture.

### 2026-11-19
- **Edition labels** in the file editor (job page > Files, Library > Files): pick Theatrical, Extended cut,
  Director's cut, ... or type one for each movie file; Apply names it `Title (Year) - Extended cut.mkv` so Jellyfin
  shows the cuts as versions. Each movie file shows its length.
- **Fixes from ARM's open pull requests** (not merged by ARM yet): random login key per install (`ARM_SECRET_KEY` to
  set your own), escaped page messages, login on drive scan / system info / database update, and actions started from
  another website are refused. Ripping: apostrophes in labels, hybrid Blu-rays, one bad MakeMKV title, Abandon
  stopping HandBrake/ffmpeg, `t1`/`t10` file matching, failed data/music rips, dot-only titles, TMDb/OMDb timeouts,
  main-feature ranking. Details in [docs/ARM-vs-A2RM.md](docs/ARM-vs-A2RM.md#fixes-to-arm-itself).
- Also from ARM's open pull requests: music CDs (a rip stuck on a scratched CD is stopped, `MUSIC_STALL_TIMEOUT`;
  incomplete rips fail; MusicBrainz timeouts and crashes; front cover as poster), the log viewer loads in pieces,
  `APPRISE_URLS` for any other Apprise service, custom `ARM_GID` at start-up, and longer SQLite lock waits.

### 2026-11-18
- **One page per job** (Identify, Files, Disc, Details) instead of separate title search, results, custom title and
  rip-settings pages; TMDb results as a grid of large posters; the file editor is on the job page. **Library** and
  **Settings** are each one page with tabs; the A2RM settings are split over the A2RM, Hardware and Notifications tabs
  and each tab saves only its own settings. Old addresses redirect.
- **A2RM, not ARM:** the web UI, phone notifications and setting descriptions say A2RM where they mean A2RM; the
  footer and Help tab link to this repository and the ARM vs A2RM page; the "update available" check against ARM's
  GitHub (which always said there was an update) is gone; MusicBrainz sees A2RM as the client; ARM's install
  scripts were removed. Things that really are ARM's (its database, disc-ID database, copy folders, log) still say ARM.
- GitHub page: ARM vs A2RM document, install steps, this changelog; ARM's templates and workflows removed.

### 2026-11-17
- **Library > Manage files:** rename, move, merge, trash and undo anything in `movies/`, `tv/` and `unidentified/`,
  including files A2RM didn't make; preview before every change.
- **Library health check:** weekly read-only check of every library file (unreadable, unfinalized, too short,
  duplicates).
- **`.nfo` files** with the TMDb id so Jellyfin matches exactly; optional tinyMediaManager scrape.
- **Naming patterns** for movies, episodes and season folders.
- **TheDiscDB lookup** (opt-in) and **title-card OCR** (opt-in) for identification.
- **TV:** pick the season and first episode, switch a job between movie and TV show, clearer search results; "play
  all" titles found by length so DVD episodes aren't skipped.
- **Fixes:** a wrong disc-ID database entry no longer wins over a better label match (Doctor Who / King Kong Escapes);
  titles use TMDb's spelling instead of the label's hyphens.
- README: install steps, tests, from-scratch build notes.

### 2026-11-16
- **"Start now"** buttons (home page, job page, phone notification).
- **Unsure discs go to `unidentified/`** when nobody answers, with stills to recognise them.
- **Hardware encoder check** before ripping, with CPU fallback; **Hardware check** page; the encoder each job used.
- **Unfinalized MakeMKV files** caught and retried; repair script.
- Language settings as drop-downs, grouped under **Languages**.
- A database lock at the end of the manual wait no longer kills the job.
- Drives that aren't passed into the container aren't shown.

### 2026-11-14
- **Docker image:** current MakeMKV at build time, optional Intel QuickSync (`INTEL_QSV=1`) without `--privileged`,
  `a2rm-check`, host check and MakeMKV update scripts, [QuickSync guide](docs/quicksync.md).
- **Language versions:** stills, length and tracks for each version, "Keep this one".
- Banner buttons: accept, pick another, cancel, delete files, dismiss.

### 2026-11-13
- **Automation:** confidence score, phone notifications with buttons (ntfy), title analysis (main feature, cuts,
  language versions, duplicates, extras, episodes), Jellyfin-friendly names, already-ripped and disk-space checks,
  finished-file checks, MakeMKV retries, stuck-job watchdog, Jellyfin scan and match check, daily backup, cleanup and
  summary.

### 2026-11-12
- **New web UI:** drive bays on the home page, poster library, phone layout, light and dark themes.

### 2026-11-10 to 2026-11-12
- Fixes to ARM 2.24.4 (most offered to ARM as pull requests #1893-#1907): MakeMKV left running, wrong DVD titles,
  "fatal error" on eject, stuck drives, the 99-title check, MakeMKV failure reasons, missing disc labels, UI title
  edits overwritten, `arm.yaml` rewrites, abandon signalling the wrong process, small crashes, secrets in logs,
  MusicBrainz and data-disc edge cases, unlabelled Blu-rays, a slow disc-ID API.

## ARM (before the fork)

ARM's own changelog as it was in the repository; it stops at v2.5.0. ARM's later changes are in its
[releases](https://github.com/automatic-ripping-machine/automatic-ripping-machine/releases).



### Current pre-release v2.5.0
  - Only one large item for this version.

    Now added the possibility for users to use [TMDB](https://developers.themoviedb.org/3/getting-started/introduction) as their metadata provider, this only works for movies at the moment. 
    In time this will have the same functionality as OMDB (movies and tv shows)
    The idea driving this is that users should be able to choose their metadata provider.
    I also think this is safer route in the long term, its better to have multiple options than only focus on one provider.
    
    - Added method to let users send/submit their correctly identified movies to a new crc64 API (an api key required)
    - Added check for crc64 from remote database

### v2.4.6
 - Updated jquery tablesorter, old version was vulnerable to XSS 
 - Removed all unused versions of CSS 
 - Smalls validation checks when searching the database page. (searching now requires min 3 chars)
 - Small changes to index.html (home for arm ui) to warn before abandoning jobs
 - Jquery ui now fully removed. Now uses only bootstrap for theming
 - ARM ui database no longer needs logfiles in url (potentially dangerous), it checks the database instead for the logfile matching the job.
 - Some progress on converting all string types to fstrings where possible
 - ARM will now break out of wait if the user inputs/searches for movie/series.

### v2.3.4 - v2.4.5
 - Adding bypass for db.session.commit() error for movies (WIP only MakeMKV and part of handbrake is coded)
 - Abandon job option added to main ARM ui page (for now this only sets job to failed no processes are cancelled)
 - Typo fixes (ARM ui images has/had some typos these have been updated and corrected)
 - ARM ui now shows CPU temps
 - ARM ui now uses percentage bars to more clearly display storage and RAM usage 
 - ARM ui database page is now fully functional with updated ui that looks clearer and with more details
 - ARM ui database is now searchable
 - ARM ui settings page now fully works and saves your settings to the arm.yaml
 - ARM ui now prevents logfiles that contain "../"
 - ARM ui login page updated to look smoother look
 - Bugfix (ARM will no longer log failures when opening sdx or hdx devices)
 - Bugfix (ARM will no longer crash when dealing with non utf-8 chars)
 - Bugfix (ARM database_updater() was setting incorrect values into the database)
 - Bugfix (ARM ui will no longer crash when trying to read logs with non utf-8 chars)
 - Bugfix (ARM ui will now get the latest % of encode, this was buggy as it was getting the very first % it could find)
 - Bugfix (ARM ui will now correctly display ram usage)
 - Bugfix (ARM ui now correctly deals with setup (it should now create all necessary folders and do so without errors) )
 - Bugfix (ARM ui update title no longer shows html on update)

### v2.3.4
 - Travisci/flake8 code fixes
 - github actions added
 - Bugfix(small bugfix for datadiscs)
 - Bugfix (old versions of yaml would cause Exceptions)
 - Bugfix (db connections are now closed properly for CD's)
 - added bypass for music CD's erroring when trying to commit to the db at the same time

### v2.3.3

 - A smaller more manageable update this time 
 - Early changes for cleaner looking log output
  - Security (HandBrake.py outputs the new prettytable format)
  - Bugfix (Transcode limit is now respected) 
  - Bugfix (Bluray disc with no titles will now be handled correctly and will not throw an exception )
  - Bugfix (abcde.config now correctly uses musicbrainz)

### v2.3.2
 - Added prettytables for logging
 - Remove api/keys from the Config class string printout 
  - Security (HandBrake.py was still outputting all api key and secrets to log file. This has now been fixed)
  - Bugfix (Transcode limit is now respected) 
  - Bugfix (Bluray disc with no titles will now be handled correctly and will not throw an exception ) 

### v2.2.0
 - Added Apprise notifications
  - Added more to the Basic web framework (flask_login)
    - Added login/admin account
    - Added dynamic websever ip to notifications
    - Allow Deleting entries from the db (also warning for both the page and every delete)
    - Added music CD covers (provied by musicbrainz & coverartarchive.org)
    - Added CPU/RAM info on index page
    - Added some clearer display for the listlogs page 
    - Bugfix (Mainfeature now works when updating in the ui) 
    - Bugfix (Job is no longer added twice when updated in ui) 
    - ALPHA: Added ARM settings page (This only shows settings for the moment, no editing)
  - Added Intel QuickSync Video support
  - Added AMD VCE support
  - Added desktop notifications
  - Added user table to the sqlite db
  - Added Debian Installer Script
  - Added Ubuntu Installer Script
  - Added Auto Identfy of music CD's
  - Made changes to the setup logging to allow music CD's to use the their artist name and album name as the log file 
  - Added abcde config file overide (This lets you give a custom config file to abcde from anywhere)
  - Added log cleaner function to strip out secret keys (This isnt complete yet)
  - Bugfix (datadiscs with no label no longer fail) 
  - Bugfix (NONE_(timestamp).log will no longer be generated ) 

### v2.1.0
 - Added new package (armui) for web user interface
  - Basic web framework (Flask, Bootstrap)
    - Retitle functionality
    - View or download logs of active and past rips
  - sqlite db

### v2.0.1
 - Fixed crash inserting bluray when bdmt_eng.xml file is not present
 - Fixed error when deleting non-existent raw files
 - Fixed file extension config parameter not being honored when RIPMETHOD='mkv'
 - Fixed media not being moved when skip_transcode=True
 - Added logic for when skip_trancode=True to make it consistant with standard processing
 - Removed systemd and reimplemented arm_wrapper.sh (see Readme for upgrade instructions)

### v2.0.0
 - Rewritten completely in Python
 - Run as non-root
 - Seperate HandBrake arguments and profiles for DVD's and Bluray's
 - Set video type or automatically identify
 - Better logging
-  Auto download latest keys_hashed.txt and KEYDB.cfg

### v1.3.0
 - Get Title for DVD and Blu-Rays so that media servesr can identify them easily.
 - Determine if video is Movie or TV-Show from OMDB API query so that different actions can be taken (TV shows usually require manual episode identification)
 - Option for MakeMKV to rip using backup method.
 - Option to rip only main feature if so desired.

### v1.2.0
- Distinguish UDF data from UDF video discs

### v1.1.1

- Added devname to abcde command
- Added logging stats (timers). "grep STAT" to see parse them out.

### v1.1.0

- Added ability to rip from multiple drives at the same time
- Added a config file for parameters
- Changed logging
  - Log name is based on ID_FS_LABEL (dvd name) variable set by udev in order to isolate logging from multiple process running simultaneously
  - Log file name and path set in config file
  - Log file cleanup based on parameter set in config file
- Added phone notification options for Pushbullet and IFTTT
- Remove MakeMKV destination directory after HandBrake finishes transcoding
- Misc stuff

### v1.0.1

- Fix ripping "Audio CDs" in ISO9660 format like LOTR.

### v1.0.0

- Initial Release
