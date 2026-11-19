"""
ARM route blueprint for jobs pages
Covers
- jobdetail [GET]
- jobdetailload [POST]
- updatetitle / customTitle [GET]: save a title (from the job page's Identify section)
- identify/search, identify/details [JSON GET]: Identify section
- titlesearch, list_titles, gettitle, select_title, changeparams, activerips: old pages, now redirects
  into the job page (C38)
- json [JSON GET]
"""

import json
import os
from flask_login import LoginManager, login_required, current_user  # noqa: F401
from flask import render_template, request, Blueprint, flash, redirect, url_for
from werkzeug.routing import ValidationError

import arm.ui.utils as ui_utils
from arm.ui import app, db, constants, json_api
from arm.models.job import Job, JobState
from arm.models.notifications import Notifications
import arm.config.config as cfg
from arm.ui.forms import ChangeParamsForm, TrackFormDynamic

route_jobs = Blueprint('route_jobs', __name__,
                       template_folder='templates',
                       static_folder='../static')


def _title_base(job):
    """Folder/file name the job's current title gives (as the ripper names them)"""
    from arm.automation import organize
    from arm.ripper import utils as ripper_utils
    try:
        return organize.safe_name(ripper_utils.fix_job_title(job))
    except AttributeError:
        return None


def _automation_setting(key):
    from arm.automation import settings
    return settings.get(key)


def _automation_view(job):
    """Automation record for the job page: confidence, titles, files, checks"""
    from arm.automation import organize, store
    data = store.load_job(job.job_id)
    if data.get("titles") and "files" in data:
        # Jobs organized before outcomes were recorded: work them out from the files
        data["titles"] = organize.with_outcomes(data["titles"], data["files"])
        if not data["titles"].get("ambiguous_language"):
            data["attention"] = [a for a in data.get("attention") or [] if a.get("kind") != "language"]
    data["versions"] = _versions(data)
    if data.get("stills"):
        from arm.automation import unidentified
        data["still_urls"] = [unidentified.still_url(job.job_id, index) for index in range(len(data["stills"]))]
    if getattr(job, "video_type", None) == "series" and getattr(job, "imdb_id", None):
        data["season_choices"] = _season_choices(job.imdb_id)
    return data


def _size_text(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"


def _clock(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}" if seconds >= 3600 \
        else f"{seconds // 60}:{seconds % 60:02d}"


def _versions(data):
    """The language versions to choose between, each with stills, length, size and tracks"""
    from arm.automation import organize
    choices = organize.version_files(data)
    if len(choices) < 2:
        return []
    by_tid = {t["tid"]: t for t in (data.get("titles") or {}).get("titles") or []}
    versions = []
    for index, entry in choices:
        title = by_tid.get(entry.get("tid"), {})
        seconds = int(title.get("seconds") or 0)
        times = [at for at in (90, 300, 600) if not seconds or at < seconds - 30] or [max(1, seconds // 2)]
        versions.append({"index": index, "tid": entry.get("tid"), "name": os.path.basename(entry["file"]),
                         "label": "Main feature" if entry.get("role") == "main" else (title.get("label") or ""),
                         "length": _clock(seconds) if seconds else "", "size": _size_text(entry["file"]),
                         "audio": title.get("audio") or [], "subtitles": title.get("subtitles") or [],
                         "stills": [{"at": at, "clock": _clock(at)} for at in times]})
    return versions


def _retitle_finished(job, old_path, old_base):
    """The title changed: clear "not sure" banners; for a finished job, move and rename its files to match"""
    if getattr(job, "job_id", None) is not None:
        from arm.automation import store
        store.clear_attention(job.job_id, "confidence")
    if getattr(job, "status", None) != JobState.SUCCESS.value or not old_path or not old_base:
        return
    try:
        from arm.automation import jellyfin, organize
        new_dir = organize.retitle(job, old_path, old_base)
    except Exception as error:  # noqa: BLE001
        app.logger.exception(f"Couldn't move the files of job {job.job_id}")
        flash(f"The title was saved, but the files couldn't be moved: {error}", "danger")
        return
    if new_dir:
        job.path = new_dir
        db.session.commit()
        jellyfin.announce(job, new_dir, old_path)
        flash(f"Moved the files to {new_dir}", "success")


@route_jobs.route('/jobdetail')
@login_required
def jobdetail():
    """
    Page for showing in-depth details about a job

    Shows Job/Config/Track class details
    displays them in a clear and easy to ready format
    """
    manual_edit = False

    # Initialise form
    track_form = TrackFormDynamic()

    job_id = request.args.get('job_id')
    if (job := Job.query.get(job_id)) is None:
        raise ValueError('Job not found')

    # Check if a manual job, waiting for input and user has not provided input
    if job.manual_mode and job.status == JobState.MANUAL_WAIT_STARTED.value and not job.manual_start:
        manual_edit = True

    # Get Job and Track data
    tracks = job.tracks.all()
    track_form.track_ref.min_entries = len(tracks)
    app.logger.debug(f"Found [{len(tracks)}] tracks")
    track_form.track_ref.entries.clear()
    # Loop through each track entry and build the WTForms dynamically
    for track_row in tracks:
        track_form.track_ref.append_entry({'track_ref': track_row.track_id,
                                           'checkbox': track_row.process})
    # For Jobs that are not waiting and in manual mode, disable the process checkbox
    if not manual_edit:
        for entry in track_form.track_ref.entries:
            entry.checkbox.render_kw = {'disabled': 'disabled'}

    search_results = ui_utils.metadata_selector("get_details", job.title, job.year, job.imdb_id)

    if search_results and 'Error' not in search_results:
        job.plot = search_results['Plot'] if 'Plot' in search_results else "There was a problem getting the plot"
        job.background = search_results['background_url'] if 'background_url' in search_results else None

    return render_template('jobdetail.html',
                           jobs=job,
                           tracks=tracks,
                           s=search_results,
                           manual_edit=manual_edit,
                           form=track_form,
                           automation=_automation_view(job),
                           start_now_buttons=_automation_setting("START_NOW_BUTTONS"),
                           params_form=ChangeParamsForm(obj=job.config) if job.config else None,
                           search_types=SEARCH_TYPES, video_types=VIDEO_TYPES,
                           search={"q": request.args.get("q") or "", "year": request.args.get("year") or "",
                                   "type": _search_type() if request.args.get("type") else _default_search_type(job),
                                   "pick": request.args.get("pick") or ""},
                           editor=_files_editor(job))


def _files_editor(job):
    """Job page > Files: the library editor (C35/C37) for the job's folder, when it is in the library"""
    from arm.automation import library
    from arm.ui.automation import library_files
    if getattr(job, "status", None) != JobState.SUCCESS.value or not getattr(job, "path", None):
        return None
    try:
        item_id = library.item_of(job.path)
        return library_files.editor_context(item_id) if item_id else None
    except Exception as error:  # noqa: BLE001 - the rest of the page works without it
        app.logger.info(f"Job {job.job_id}: no file editor ({error})")
        return None


@route_jobs.route('/jobdetailload', methods=['POST'])
@login_required
def jobdetail_load():
    """
    Process updated track ID fields against a job and load to the ARM database if valid
    All data passed via POST
    """
    # Initialise form
    track_form = TrackFormDynamic()

    job_id = request.args.get('job_id')
    job = Job.query.get(job_id)

    # Data passed back from webpage, process and update track fields
    if request.method == 'POST' and track_form.validate_on_submit():
        app.logger.debug(f"Job id [{job.job_id}]")
        app.logger.debug(f"Returned [{len(track_form.track_ref.entries)}] tracks")
        for track_row in track_form.track_ref.entries:
            # app.logger.debug(f"Track deets [{track_row}]")
            track_id = track_row.data['track_ref']
            checkbox_value = track_row.data['checkbox']
            app.logger.debug(f"Setting [{track_id}] to [{checkbox_value}]")

            db_track = job.tracks.filter_by(track_id=track_id).first()
            if db_track:
                db_track.process = checkbox_value
                db.session.commit()

        # Set job to ready
        job.manual_start = True
        db.session.commit()
        app.logger.debug(f"Setting [{job.job_id}] to [{job.manual_start}], lets get ripping")
        flash("Tracks was updated", "success")

    return redirect(url_for('route_jobs.jobdetail', job_id=job_id))


SEARCH_TYPES = {"both": "Movies and TV shows", "movie": "Movies", "series": "TV shows"}
VIDEO_TYPES = {"movie": "Movie", "series": "TV show"}


def _search_type():
    """What the title search looks for: both (default), movie or series"""
    value = request.args.get("type", "both")
    return value if value in SEARCH_TYPES else "both"


def _default_search_type(job):
    """Search TV shows first for a job that is already a TV show, otherwise both"""
    return "series" if getattr(job, "video_type", None) == "series" else "both"


def _job_page(job_id, section="", **params):
    """The job page (C38: one page per job), opened at a section"""
    return url_for('route_jobs.jobdetail', job_id=job_id, **params) + (f"#{section}" if section else "")


@route_jobs.route('/titlesearch')
@login_required
def title_search():
    """Old title search page: now the job page's Identify section (searches straight away when a title is given)"""
    params = {"q": request.args.get("title"), "year": request.args.get("year"),
              "type": request.args.get("type")} if request.args.get("title") else {}
    return redirect(_job_page(request.args.get('job_id'), "identify", **params))


@route_jobs.route('/customTitle')
@login_required
def customtitle():
    """
    For setting custom title for series with multiple discs (the form is in the job page's Identify section)
    """
    job_id = request.args.get('job_id')
    ui_utils.job_id_validator(job_id)
    job = Job.query.get(job_id)
    if request.args.get("title"):
        # Clean the title like updatetitle does: it becomes part of folder and file names
        new_title = ui_utils.clean_for_filename(request.args.get("title"))
        old_path, old_base = getattr(job, "path", None), _title_base(job)
        args = {
            'title': new_title,
            'title_manual': new_title,
            'year': request.args.get("year"),
            'year_manual': request.args.get("year")
        }
        if request.args.get("type") in VIDEO_TYPES:
            args['video_type'] = args['video_type_manual'] = request.args.get("type")
        notification = Notifications(f"Job: {job.job_id} was updated",
                                     f'Title: {job.title} ({job.year}) was updated to '
                                     f'{new_title} ({request.args.get("year")})')
        db.session.add(notification)
        ui_utils.database_updater(args, job)
        flash(f'Custom title changed. Title={job.title}, Year={job.year}.', "success")
        _retitle_finished(job, old_path, old_base)
        return redirect(_job_page(job.job_id))
    return redirect(_job_page(job_id, "identify"))


@route_jobs.route('/gettitle')
@route_jobs.route('/select_title')
@login_required
def gettitle():
    """Old result-details page: now a panel in the job page's Identify section"""
    imdb_id = request.args.get('imdbID').strip() if request.args.get('imdbID') else None
    job_id = request.args.get('job_id').strip() if request.args.get('job_id') else None
    if imdb_id == "" or imdb_id is None:
        app.logger.debug("gettitle - no imdb supplied")
        flash("No imdb supplied", "danger")
        raise ValidationError("No imdb supplied")
    if job_id == "" or job_id is None:
        app.logger.debug("gettitle - no job supplied")
        flash(constants.NO_JOB, "danger")
        raise ValidationError(constants.NO_JOB)
    return redirect(_job_page(job_id, "identify", pick=imdb_id))


def _poster(url, size):
    """A TMDb poster at another size (w185, w342, ...); other URLs as they are"""
    url = url or ""
    return url.replace("/t/p/original/", f"/t/p/{size}/") if "/t/p/original/" in url else url


@route_jobs.route('/identify/search')
@login_required
def identify_search():
    """Job page > Identify: search results as JSON (same search as the old results page)"""
    title = (request.args.get('title') or '').strip()[:200]
    year = (request.args.get('year') or '').strip()[:4]
    media_type = _search_type()
    if not title:
        return {"results": [], "message": "Type a title to search for."}
    results = ui_utils.metadata_selector("search", title, year, media_type=media_type)
    message = ""
    if results is None or 'Error' in results or ('Search' in results and len(results['Search']) < 1):
        if year:
            message = f"Nothing found for {title} ({year}), so this is without the year."
            results = ui_utils.metadata_selector("search", title, "", media_type=media_type)
    found = [res for res in (results or {}).get("Search") or []
             if "Type" in res and str(res["Type"]).lower() != "game"]
    if not found:
        message = f"Nothing found for {title}."
    return {"message": message, "results": [
        {"title": res.get("Title") or "", "year": res.get("Year") or "", "imdb_id": res.get("imdbID") or "",
         "type": str(res.get("Type") or "").lower(),
         "type_label": "TV show" if str(res.get("Type")).lower() == "series" else str(res.get("Type")).capitalize(),
         "poster": _poster(res.get("Poster"), "w342") if str(res.get("Poster") or "").startswith("http") else "",
         "details": res.get("Details") or "", "plot": res.get("Plot") or ""} for res in found]}


@route_jobs.route('/identify/details')
@login_required
def identify_details():
    """Job page > Identify: one result's full details and TV seasons, as JSON"""
    imdb_id = (request.args.get('imdbID') or '').strip()
    if not imdb_id:
        return {"error": "No IMDb id."}, 400
    info = ui_utils.metadata_selector("get_details", None, None, imdb_id) or {}
    if not info or 'Error' in info:
        return {"error": f"Couldn't load the details for {imdb_id}."}
    kind = str(info.get("Type") or "").lower()
    return {"title": info.get("Title") or "", "year": info.get("Year") or "", "imdb_id": info.get("imdbID") or imdb_id,
            "type": kind, "poster": info.get("Poster") or "", "poster_big": _poster(info.get("Poster"), "w500"),
            "plot": info.get("Plot") or "", "seasons": _season_choices(imdb_id) if kind == "series" else []}


def _season_choices(imdb_id):
    """TMDb's seasons for the Season drop-down (Settings > Automation > Let me pick the season)"""
    from arm.automation import settings, tmdb
    if not settings.get("TV_SEASON_CHOICE"):
        return []
    try:
        return tmdb.season_choices(imdb_id)
    except Exception as error:  # noqa: BLE001 - the page works without it
        app.logger.info(f"Couldn't load the seasons for {imdb_id}: {error}")
        return []


@route_jobs.route('/updatetitle')
@login_required
def updatetitle():
    """
    used to save the details from the search
    Example URL
    updatetitle?title=Home&amp;year=2015&amp;imdbID=tt2224026&amp;type=movie&amp;
    poster=http://image.tmdb.org/t/p/original/usFenYnk6mr8C62dB1MoAfSWMGR.jpg&amp;job_id=109

    args
    title - new movie title
    year - new movie year
    imdbID - new movie IMDB reference
    type - new movie type
    poster - new movie poster URL
    job_id - job to update
    """
    #

    job_id = request.args.get('job_id')
    job = Job.query.get(job_id)
    old_title = job.title
    old_year = job.year
    old_path, old_base = getattr(job, "path", None), _title_base(job)
    app.logger.debug(f"Old Title and Year: {old_title}, {old_year}")

    app.logger.debug(f"New Title: {request.args.get('title')}")
    new_title = ui_utils.clean_for_filename(request.args.get('title'))
    app.logger.debug(f"Cleaned New Title: {new_title}")
    job.title = job.title_manual = new_title

    app.logger.debug(f"New Year: {request.args.get('year')}")
    app.logger.debug(f"New Type: {request.args.get('type')}")
    app.logger.debug(f"New IMDB: {request.args.get('imdbID')}")
    app.logger.debug(f"New Poster: {request.args.get('poster')}")

    job.year = job.year_manual = request.args.get('year')
    job.video_type = job.video_type_manual = request.args.get('type')
    job.imdb_id = job.imdb_id_manual = request.args.get('imdbID')
    job.poster_url = job.poster_url_manual = request.args.get('poster')

    job.hasnicetitle = True
    if request.args.get('type') == "series" and request.args.get('season', '').strip():
        from arm.automation import organize
        try:
            organize.save_season(job.job_id, request.args.get('season'), request.args.get('first_episode'))
        except ValueError:
            flash("The season and first episode must be numbers.", "danger")
    notification = Notifications(f"Job: {job.job_id} was updated",
                                 f'Title: {old_title} ({old_year}) was updated to '
                                 f'{request.args.get("title")} ({request.args.get("year")})')
    db.session.add(notification)
    db.session.commit()
    flash(f'Title: {old_title} ({old_year}) was updated to '
          f'{request.args.get("title")} ({request.args.get("year")})', "success")
    _retitle_finished(job, old_path, old_base)
    return redirect(_job_page(job.job_id))


@route_jobs.route('/activerips')
@login_required
def rips():
    """Old active-rips page (unlinked; the home page lists active jobs)"""
    return redirect("/")


@route_jobs.route('/changeparams')
@login_required
def changeparams():
    """Old rip-settings page: now under Details on the job page"""
    return redirect(_job_page(request.args.get('config_id'), "rip-settings"))


@route_jobs.route('/list_titles')
@login_required
def list_titles():
    """Old search results page: now the job page's Identify section"""
    job_id = request.args.get('job_id').strip() if request.args.get('job_id') else ''
    if job_id == "":
        app.logger.debug("list_titles - no job supplied")
        flash(constants.NO_JOB, "danger")
        raise ValidationError
    return redirect(_job_page(job_id, "identify", q=request.args.get('title') or None,
                              year=request.args.get('year') or None, type=request.args.get('type') or None))


@route_jobs.route('/json', methods=['GET'])
def feed_json():
    """
    json mini API
    This is used for all api/ajax calls this makes thing easier to read/code for
    Adding a new function to the api is as simple as adding a new elif where GET[mode]
    is your call
    You can then add a function inside utils to deal with the request
    """
    # Check if users is authenticated
    # Return data when authenticated, but allow basic job info when not
    authenticated = ui_utils.authenticated_state()
    mode = str(request.args.get('mode'))
    return_json = {'mode': mode, 'success': False}

    if authenticated:
        # Hold valid data (post/get data) we might receive from pages - not in here ? it's going to throw a key error
        valid_data = {
            'j_id': request.args.get('job'),
            'searchq': request.args.get('q'),
            'logpath': cfg.arm_config['LOGPATH'],
            'fail': 'fail',
            'success': 'success',
            'joblist': 'joblist',
            'mode': mode,
            'config_id': request.args.get('config_id'),
            'notify_id': request.args.get('notify_id'),
            'notify_timeout': {'funct': json_api.get_notify_timeout, 'args': ('notify_timeout',)},
            'restart': {'funct': json_api.restart_ui, 'args': ()},
        }
        # Valid modes that should trigger functions
        valid_modes = {
            'delete': {'funct': json_api.delete_job, 'args': ('j_id', 'mode')},
            'abandon': {'funct': json_api.abandon_job, 'args': ('j_id',)},
            'full': {'funct': json_api.generate_log, 'args': ('logpath', 'j_id')},
            'search': {'funct': json_api.search, 'args': ('searchq',)},
            'getfailed': {
                'funct': json_api.get_x_jobs,
                'args': (JobState.FAILURE.value,),
            },
            'getsuccessful': {
                'funct': json_api.get_x_jobs,
                'args': (JobState.SUCCESS.value,),
            },
            'fixperms': {'funct': ui_utils.fix_permissions, 'args': ('j_id',)},
            'joblist': {'funct': json_api.get_x_jobs, 'args': ('joblist',)},
            'send_item': {'funct': ui_utils.send_to_remote_db, 'args': ('j_id',)},
            'change_job_params': {'funct': json_api.change_job_params, 'args': ('config_id',)},
            'read_notification': {'funct': json_api.read_notification, 'args': ('notify_id',)},
            'notify_timeout': {'funct': json_api.get_notify_timeout, 'args': ('notify_timeout',)},
            'dashboard': {'funct': json_api.get_dashboard, 'args': ()},
            'restart': {'funct': json_api.restart_ui, 'args': ()},
        }
    else:
        valid_data = {
            'joblist': 'joblist',
        }
        valid_modes = {
            'joblist': {'funct': json_api.get_x_jobs, 'args': ('joblist',)},
        }
    # prepare JSON data
    if mode in valid_modes:
        args = [valid_data[x] for x in valid_modes[mode]['args']]
        return_json = valid_modes[mode]['funct'](*args)
    return_json['notes'] = json_api.get_notifications()

    # return JSON data
    return app.response_class(response=json.dumps(return_json, indent=4, sort_keys=True),
                              status=200,
                              mimetype=constants.JSON_TYPE)
