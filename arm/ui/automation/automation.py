"""
Automation pages:
- /automation/act       buttons in phone notifications (signed links, no login needed)
- /automation/settings  Settings > Automation
- /automation/health    Settings > Automation > Library health
- /automation/frame     still frames from finished files (to tell language versions apart)
"""
import json
import os
import shutil
import subprocess
import time

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, session, url_for
from flask_login import login_required

import arm.config.config as cfg
from arm.automation import notify, settings, store
from arm.models.job import Job
from arm.ui import app, csrf, db

route_automation = Blueprint('route_automation', __name__, template_folder='templates', static_folder='../static')

ACTIONS = {"confirm": "Keep this title", "suggest": "Use the suggested title", "start": "Start ripping now"}


def start_now(job):
    """
    End ARM's manual wait now, keeping the current title as it is (unlike confirm_title, the
    title isn't marked as confirmed). The ripper's wait loop checks this every 5 seconds.
    :return: False if the job isn't waiting
    """
    if job.status != "waiting" or not settings.get("START_NOW_BUTTONS"):
        return False
    store.update_job(job.job_id, start_now=True)
    return True


def confirm_title(job):
    """The user says the automatic title is right: make it a manual value, which ends ARM's wait"""
    job.title_manual = job.title
    job.year_manual = job.year
    job.video_type_manual = job.video_type
    job.imdb_id_manual = job.imdb_id
    job.poster_url_manual = job.poster_url
    job.hasnicetitle = True
    db.session.commit()
    data = store.load_job(job.job_id)
    confidence = dict(data.get("confidence") or {}, confirmed=True)
    store.update_job(job.job_id, confidence=confidence)
    store.clear_attention(job.job_id, "confidence")


@route_automation.route('/automation/act', methods=['GET', 'POST'])
@csrf.exempt
def act():
    """Phone buttons. GET shows a page with a button (for browsers), POST does it."""
    job_id = request.values.get("job", type=int)
    action = request.values.get("action", "")
    token = request.values.get("token", "")
    if job_id is None or action not in ACTIONS or not notify.check_signature(job_id, action, token):
        abort(403)
    job = Job.query.get(job_id)
    if job is None:
        abort(404)
    if request.method == "GET":
        return render_template("automation_act.html", job=job, action=action, label=ACTIONS[action],
                               token=token, done=False)
    if action == "confirm":
        if not move_if_unidentified(job):
            confirm_title(job)
        app.logger.info(f"Job {job_id}: title confirmed from a notification")
    elif action == "suggest":
        if not apply_suggestion(job):
            abort(404)
    elif action == "start":
        if not start_now(job):
            return render_template("automation_act.html", job=job, action=action, label=ACTIONS[action],
                                   token=token, done=True, not_waiting=True)
        app.logger.info(f"Job {job_id}: wait ended from a notification")
        app.logger.info(f"Job {job_id}: suggested title taken from a notification")
    if request.headers.get("Accept", "").startswith("text/html") or request.form.get("from_page"):
        return render_template("automation_act.html", job=job, action=action, label=ACTIONS[action],
                               token=token, done=True)
    return {"success": True, "job": job_id, "action": action}


def _deletable_folder(job):
    """The job's output folder, if it's safe to delete: inside COMPLETED_PATH/<type>/ and not used by another job"""
    if not job.path:
        return None
    completed = os.path.realpath(str(cfg.arm_config["COMPLETED_PATH"]))
    folder = os.path.realpath(job.path)
    relative = os.path.relpath(folder, completed)
    if relative.startswith("..") or len(relative.split(os.sep)) < 2 or not os.path.isdir(folder):
        return None
    others = Job.query.filter(Job.path == job.path, Job.job_id != job.job_id, Job.status == "success").count()
    return None if others else folder


@route_automation.route('/automation/job_action', methods=['POST'])
@login_required
def job_action():
    """
    Buttons on the home page banner and the job page:
    accept (keep ARM's guessed title), start_now (end the manual wait now), cancel (stop a
    running job), delete_files (remove a finished job's output folder), dismiss (hide a banner
    item), keep_version (keep one language version and delete the others), keep_all (keep every
    language version), set_type (movie <-> TV show), use_suggestion (take the title A2RM suggested).
    """
    job_id = request.form.get("job", type=int)
    action = request.form.get("action", "")
    job = Job.query.get(job_id) if job_id is not None else None
    if job is None:
        return {"success": False, "message": "Job not found"}, 404
    if action in ("keep_version", "keep_all"):
        return _version_action(job, action)
    if action in ("set_type", "use_suggestion"):
        return _title_action(job, action)
    handler = {"accept": _accept, "start_now": _start_now, "dismiss": _dismiss, "cancel": _cancel,
               "delete_files": _delete_files, "set_season": _set_season}.get(action)
    if handler is None:
        return {"success": False, "message": "Unknown action"}, 400
    name = job.title_manual or job.title or job.label or f"job {job_id}"
    result = handler(job, name)
    if isinstance(result, tuple):
        return {"success": False, "message": result[0]}, result[1]
    return {"success": True, "message": result}


def _accept(job, name):
    if not job.finished:
        confirm_title(job)
        return f"Keeping {name}. A2RM starts ripping when its wait ends."
    if move_if_unidentified(job):
        return f"Kept {name} and moved its files to {job.path}."
    store.clear_attention(job.job_id, "confidence")
    return f"Kept {name}."


def move_if_unidentified(job):
    """
    A finished job filed as unidentified (nobody answered in time): accepting its title confirms it
    and moves the folder to movies/TV under that title.
    :return: True if the files were moved
    """
    path = str(getattr(job, "path", "") or "").replace("\\", "/")
    if not getattr(job, "finished", False) or "/unidentified/" not in path:
        return False
    if not store.load_job(job.job_id).get("unidentified"):
        return False
    from arm.ui.jobs import jobs as jobs_routes
    old_path, old_base = job.path, jobs_routes._title_base(job)
    confirm_title(job)
    jobs_routes._retitle_finished(job, old_path, old_base)
    return job.path != old_path


def _set_season(job, _name):
    """TV jobs: the season (and first episode) on this disc, used by episode naming"""
    from arm.automation import organize
    if job.video_type != "series":
        return "Only TV shows have seasons.", 400
    try:
        choice = organize.save_season(job.job_id, request.form.get("season"), request.form.get("first_episode"))
    except ValueError:
        return "The season and first episode must be numbers.", 400
    if choice is None:
        return "Season will be guessed from the disc label."
    first = f", from episode {choice['first_episode']}" if choice["first_episode"] else ""
    note = " Files already named keep their names." if job.finished else ""
    return f"Season {choice['season']}{first}.{note}"


def _start_now(job, name):
    if not start_now(job):
        return "This job isn't waiting any more.", 400
    return f"Starting {name} within a few seconds."


def _dismiss(job, _name):
    kind = request.form.get("kind") or "confidence"
    store.clear_attention(job.job_id, kind)
    if kind == "failed":
        # The "failed in the last 24 hours" banner isn't a stored item; remember it was dismissed
        store.update_job(job.job_id, dismissed_failure=True)
    return "Dismissed."


def _cancel(job, name):
    if job.finished:
        return "This job has already finished.", 400
    from arm.ui import json_api
    result = json_api.abandon_job(str(job.job_id))
    if not result.get("success"):
        return result.get("Error") or "Couldn't stop the job.", 500
    for item in store.load_job(job.job_id).get("attention") or []:
        store.clear_attention(job.job_id, item.get("kind"))
    return f"Stopped {name} and ejected the disc."


def _delete_files(job, _name):
    if not job.finished:
        return "Stop the job first.", 400
    folder = _deletable_folder(job)
    if folder is None:
        return "There's no output folder A2RM can safely delete for this job.", 400
    shutil.rmtree(folder)
    app.logger.info(f"Job {job.job_id}: deleted its output folder {folder} from the web UI")
    store.update_job(job.job_id, files_deleted=folder, attention=[])
    from arm.automation import jellyfin
    jellyfin.announce(None, folder)
    return f"Deleted {folder}."


def apply_suggestion(job, commit=True):
    """Take the title A2RM suggested for a low-confidence job (as a manual value: ends ARM's wait)"""
    suggestion = (store.load_job(job.job_id).get("confidence") or {}).get("suggestion")
    if not suggestion:
        return None
    from arm.ui import utils as ui_utils
    job.title = job.title_manual = ui_utils.clean_for_filename(suggestion["title"])
    job.year = job.year_manual = suggestion.get("year") or ""
    job.video_type = job.video_type_manual = suggestion.get("video_type") or "movie"
    job.imdb_id = job.imdb_id_manual = suggestion.get("imdb_id")
    job.poster_url = job.poster_url_manual = suggestion.get("poster_url")
    job.hasnicetitle = True
    if commit:
        db.session.commit()
        data = store.load_job(job.job_id)
        store.update_job(job.job_id, confidence=dict(data.get("confidence") or {}, confirmed=True))
        store.clear_attention(job.job_id, "confidence")
    return suggestion


def _title_action(job, action):
    """
    set_type: switch the job between movie and TV show (the title stays). use_suggestion: take
    the title A2RM found as a likely alternative. Either way the value becomes a manual one (which
    ends ARM's wait), and a finished job's files move to the matching folder.
    """
    from arm.ui.jobs import jobs as jobs_routes
    old_path, old_base = getattr(job, "path", None), jobs_routes._title_base(job)
    if action == "set_type":
        new_type = request.form.get("type", "")
        if new_type not in ("movie", "series"):
            return {"success": False, "message": "Pick movie or TV show."}, 400
        if not job.title_manual:
            confirm_title(job)  # keep the current title as the user's choice
        job.video_type = job.video_type_manual = new_type
        message = f"{job.title_manual or job.title} is now a {'TV show' if new_type == 'series' else 'movie'}."
    else:
        suggestion = apply_suggestion(job, commit=False)
        if not suggestion:
            return {"success": False, "message": "There's no suggestion for this job."}, 400
        message = f"Using {suggestion['title']}" + (f" ({job.year})" if job.year else "") + "."
    db.session.commit()
    data = store.load_job(job.job_id)
    store.update_job(job.job_id, confidence=dict(data.get("confidence") or {}, confirmed=True))
    jobs_routes._retitle_finished(job, old_path, old_base)
    app.logger.info(f"Job {job.job_id}: {message}")
    return {"success": True, "message": message}


def _version_action(job, action):
    """Language versions ARM couldn't tell apart: keep the one picked (deleting the others), or all"""
    from arm.automation import organize
    if not job.finished:
        return {"success": False, "message": "Wait for the job to finish first."}, 400
    if action == "keep_all":
        organize.keep_all_versions(job)
        return {"success": True, "message": "Keeping every version."}
    try:
        kept, deleted = organize.keep_version(job, request.form.get("file", type=int))
    except ValueError as error:
        return {"success": False, "message": str(error)}, 400
    app.logger.info(f"Job {job.job_id}: kept {kept}, deleted {deleted} from the web UI")
    return {"success": True, "message": f"Kept {os.path.basename(kept)} and deleted {len(deleted)} other "
                                        f"version{'s' if len(deleted) != 1 else ''}."}


def _form_value(setting):
    if setting.kind == "bool":
        return setting.key in request.form
    if setting.kind == "langs":
        return ",".join(request.form.getlist(setting.key))
    return request.form.get(setting.key, "")


@route_automation.route('/automation/settings', methods=['GET', 'POST'])
@login_required
def automation_settings():
    session["page_title"] = "Automation"
    if request.method == "POST":
        user_config = cfg._load_config(cfg.arm_config_path) or {}
        changed = []
        for setting in settings.SCHEMA:
            raw = _form_value(setting)
            if setting.kind == "secret" and raw == "":
                continue  # empty = keep the stored secret
            value = settings.coerce(setting, raw)
            if user_config.get(setting.key) != value:
                changed.append(setting.label)
            user_config[setting.key] = value
        with open(os.path.join(cfg.arm_config["INSTALLPATH"], "arm/ui/comments.json")) as handle:
            comments = json.load(handle)
        full = dict(cfg.template_config)
        full.update(user_config)
        cfg.write_config_file(cfg.arm_config_path, cfg._render_config(full, comments))
        cfg.arm_config.update({s.key: user_config[s.key] for s in settings.SCHEMA if s.key in user_config})
        flash(f"Saved {len(changed)} change{'s' if len(changed) != 1 else ''}. New discs use them right away."
              if changed else "No changes.", "success")
        return redirect(url_for("route_automation.automation_settings"))
    groups = []
    for group, title in settings.GROUPS:
        items = []
        for setting in settings.SCHEMA:
            if setting.group != group:
                continue
            value = settings.get(setting.key)
            items.append({"s": setting, "value": "" if setting.kind == "secret" else value,
                          "is_set": bool(value) if setting.kind == "secret" else None,
                          "languages": settings.language_choices(value) if setting.kind in ("lang", "langs") else None,
                          "selected": str(value).split(",") if setting.kind in ("lang", "langs") else None})
        groups.append({"id": group, "title": title, "fields": items})
    status = {"ntfy": settings.ntfy_enabled(), "jellyfin": settings.jellyfin_enabled(),
              "last": store.load_state()}
    return render_template("automation_settings.html", groups=groups, status=status)


def _checked_text(result):
    if result and result.get("checked"):
        result = dict(result, checked_text=time.strftime("%Y-%m-%d %H:%M", time.localtime(result["checked"])))
    return result


@route_automation.route('/automation/hardware', methods=['GET', 'POST'])
@login_required
def hardware():
    """Settings > Automation > Hardware check (GPU, permissions, VA-API, HandBrake, test encode)"""
    from arm.automation import hwcheck, hwreport
    session["page_title"] = "Hardware check"
    test = decode = None
    if request.method == "POST":
        report = hwreport.report()
        if request.form.get("action") == "test_encode" and report["test_encoder"]:
            test = hwcheck.cached_test(report["test_encoder"], fresh=True)
        elif request.form.get("action") == "test_decode":
            decode = hwreport.decode_test()
    report = hwreport.report()
    report["last_test"] = _checked_text(report["last_test"])
    report["last_decode"] = _checked_text(report["last_decode"])
    return render_template("automation_hardware.html", r=report, test=_checked_text(test),
                           decode=_checked_text(decode))


@route_automation.route('/automation/health', methods=['GET', 'POST'])
@login_required
def library_health():
    """Settings > Automation > Library health: the last check's results; "Check now" runs one in the background"""
    from arm.automation import health
    session["page_title"] = "Library health"
    if request.method == "POST":
        if health.busy():
            flash("A disc is ripping or transcoding; check again when it's done.", "warning")
        else:
            subprocess.Popen(["python3", "-m", "arm.automation.maintenance", "health"], cwd="/opt/arm",
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            flash("Library check started. Reload this page in a few minutes.", "success")
        return redirect(url_for("route_automation.library_health"))
    result = store.load_state().get("health") or {}
    return render_template("automation_health.html", r=result, days=settings.get("HEALTH_CHECK_DAYS"),
                           root=str(cfg.arm_config.get("COMPLETED_PATH") or ""))


@route_automation.route('/automation/name_preview')
@login_required
def name_preview():
    """Settings > Automation: sample file name for a naming pattern (C29)"""
    from arm.automation import naming
    kinds = {"MOVIE_NAME_PATTERN": "movie", "EPISODE_NAME_PATTERN": "episode", "SEASON_FOLDER_PATTERN": "season"}
    kind = kinds.get(request.args.get("key", ""))
    if not kind:
        return {"name": ""}, 400
    return {"name": naming.preview(kind, request.args.get("pattern", "")[:200])}


@route_automation.route('/automation/test_ntfy', methods=['POST'])
@login_required
def test_ntfy():
    ok = notify.send("A2RM test notification", "Phone notifications from A2RM work.", tags=["dvd"],
                     click=notify.arm_url("/"))
    flash("Test notification sent." if ok else "Couldn't send it. Check the server and topic.",
          "success" if ok else "danger")
    return redirect(url_for("route_automation.automation_settings"))


@route_automation.route('/automation/still')
@csrf.exempt
def still():
    """Stills saved for a job filed as unidentified (signed: also opened from the phone notification)"""
    job_id = request.args.get("job", type=int)
    index = request.args.get("n", type=int)
    token = request.args.get("token")
    if job_id is None or index is None or not notify.check_signature(job_id, f"still{index}", token):
        abort(403)
    stills = store.load_job(job_id).get("stills") or []
    if not 0 <= index < len(stills) or not os.path.isfile(stills[index]["file"]):
        abort(404)
    with open(stills[index]["file"], "rb") as handle:
        return Response(handle.read(), mimetype="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@route_automation.route('/automation/frame')
@login_required
def frame():
    """A JPEG still from one of a job's finished files, at a given second"""
    job_id = request.args.get("job", type=int)
    index = request.args.get("file", type=int)
    at = max(0, request.args.get("at", default=60, type=int))
    files = store.load_job(job_id).get("files") or [] if job_id is not None else []
    if index is None or not 0 <= index < len(files):
        abort(404)
    path = files[index]["file"]
    completed = os.path.normpath(str(cfg.arm_config["COMPLETED_PATH"]))
    if not os.path.normpath(path).startswith(completed + os.sep) or not os.path.isfile(path):
        abort(404)
    try:
        image = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(at), "-i", path, "-frames:v", "1",
                                "-vf", "scale=480:-2", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
                               capture_output=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        abort(500)
    if not image:
        abort(404)
    return Response(image, mimetype="image/jpeg", headers={"Cache-Control": "max-age=86400"})
