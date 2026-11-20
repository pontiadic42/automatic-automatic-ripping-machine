"""
Library > Manage files (C35): everything in movies/, tv/ and unidentified/, including files A2RM
didn't make; rename, move, merge, trash, undo.

- /library/files        the list (rescans on every load: names and sizes only, quick)
- /library/files/item   one folder or file: title/type/season, a preview of every move, apply
- /library/files/search TMDb search for the edit page (JSON)
"""
import os

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import login_required

from arm.automation import library, organize

route_library_files = Blueprint('route_library_files', __name__, template_folder='templates',
                                static_folder='../static')

FILTERS = {"attention": "Needs attention", "all": "All", "untracked": "Not from A2RM", "duplicate": "Same title",
           "unidentified": "Unidentified", "raw_names": "MakeMKV names", "copy": "Copy folders", "loose": "Loose files"}


def size_text(size):
    for unit, factor in (("TB", 1 << 40), ("GB", 1 << 30), ("MB", 1 << 20)):
        if size >= factor:
            return f"{size / factor:.1f} {unit}"
    return f"{max(0, size) // 1024} KB"


def _active_job():
    from arm.automation import health
    return health.busy()


@route_library_files.route('/library/files', methods=['GET', 'POST'])
@login_required
def files():
    session["page_title"] = "Manage files"
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "undo":
                entry = library.undo(request.form.get("entry", type=int))
                flash(f"Undone: {entry['summary']}", "success")
            elif action == "empty_trash":
                flash(f"Trash emptied ({library.empty_trash()} item(s) deleted for good).", "success")
        except library.LibraryError as error:
            flash(str(error), "danger")
        return redirect(url_for("route_library_files.files", filter=request.args.get("filter")))
    result = library.scan()
    counts = {name: (len(result["items"]) if name == "all" else
                     sum(1 for item in result["items"] if item["attention"]) if name == "attention" else
                     sum(1 for item in result["items"] if any(i["code"] == name for i in item["issues"])))
              for name in FILTERS}
    selected = request.args.get("filter", "attention")
    selected = selected if selected in FILTERS else "attention"
    trash_count, trash_bytes = library.trash_size()
    return render_template("library_files.html", r=result, counts=counts, filters=FILTERS, selected=selected,
                           size_text=size_text, trash_count=trash_count, trash_size=size_text(trash_bytes),
                           history=library.history()[:15], root=library.completed())


def _form_values():
    form = request.form
    return {"kind": form.get("kind"), "title": form.get("title", ""), "year": form.get("year", ""),
            "imdb_id": form.get("imdb_id", ""), "season": form.get("season", ""),
            "first_episode": form.get("first_episode", "")}


def _form_editions():
    """{file: label} from the label drop-down, or the typed text when it says Other (C40)"""
    editions = {}
    for key, value in request.form.items():
        if key.startswith("file_"):
            index = key[5:]
            choice = request.form.get("edition_" + index, "")
            editions[value] = request.form.get("edition_text_" + index, "") if choice == "other" else choice
    return editions


def length_text(seconds):
    if not seconds:
        return ""
    seconds = int(round(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _form_roles():
    roles = {}
    for key, value in request.form.items():
        if key.startswith("file_"):
            roles[value] = request.form.get("role_" + key[5:], "")
    return roles


def editor_context(item_id, found=None, values=None, roles=None, preview=None, error=None, editions=None):
    """What library_editor.html needs for one item (used here and on the job page's Files section)"""
    found = found or library.find(item_id)
    values = values or library.defaults(found)
    roles = roles or library.default_roles(found["id"], values.get("kind") or "movie")
    base = library.absolute(found["id"])
    base = base if os.path.isdir(base) else os.path.dirname(base)
    editions = library.current_editions(found["id"], roles) if editions is None else editions
    file_list = [{"rel": rel, "role": role, "size": size_text(max(0, library._size(os.path.join(base, rel)))),
                  "edition": editions.get(rel, ""),
                  # lengths tell the cuts apart; ffprobe only for the main movie and its versions
                  "length": length_text(organize.duration_seconds(os.path.join(base, rel)))
                  if role in ("main", "version") else ""}
                 for rel, role in sorted(roles.items(), key=lambda pair: library._episode_order(pair[0]))]
    others = library.same_title(found, (library.store.load_state().get("library") or {}).get("items", []))
    return {"item": found, "v": values, "files": file_list, "preview": preview, "others": others,
            "size_text": size_text, "roles": library.ROLES, "error": error, "editions": library.EDITIONS}


def _job_files(job_id):
    return url_for("route_jobs.jobdetail", job_id=job_id) + "#files"


@route_library_files.route('/library/files/item', methods=['GET', 'POST'])
@login_required
def item():
    """One library item. Items from a job are edited on the job page (C37); this page is for the rest.
    POST partial=1 (from the job page) returns just the editor."""
    item_id = request.values.get("id", "")
    session["page_title"] = "Manage files"
    partial = request.values.get("partial") == "1"
    try:
        found = library.find(item_id)
    except library.LibraryError as error:
        if partial:
            return f'<p class="arm-attn bad">{error}</p>'
        flash(str(error), "danger")
        return redirect(url_for("route_library_files.files"))
    if request.method == "GET" and found.get("job_id") and request.args.get("here") != "1":
        return redirect(_job_files(found["job_id"]))
    done = (_job_files(found["job_id"]) if request.values.get("back") and found.get("job_id")
            else url_for("route_library_files.files"))
    values, roles, preview, error, editions = None, None, None, None, None
    if request.method == "POST":
        action = request.form.get("action")
        values, roles, editions = _form_values(), _form_roles(), _form_editions()
        try:
            if action in ("apply", "trash") and _active_job():
                raise library.LibraryError("A disc is ripping or transcoding; try again when it's done.")
            if action == "trash":
                library.trash(found["id"])
                flash(f"Moved {found['id']} to the trash. Undo it from the list of changes.", "success")
                return redirect(url_for("route_library_files.files"))
            if action == "apply":
                entry = library.apply(found["id"], values, roles, editions)
                flash(f"Done: {entry['summary']} ({len(entry['moves'])} file(s) moved"
                      + (f", {len(entry['left'])} already there left in place" if entry["left"] else "")
                      + "). Undo it from Library > Files.", "success")
                return redirect(done)
            preview = library.plan(found["id"], values, roles, editions)
            roles, editions = preview["roles"], preview["editions"]
        except library.LibraryError as err:
            error = str(err)
            if not partial:
                flash(error, "danger")
    context = editor_context(found["id"], found, values, roles, preview, error,
                             None if editions is None else library.clean_editions(editions))
    if partial:
        return render_template("library_editor.html", back=request.values.get("back", ""), **context)
    return render_template("library_item.html", back="", **context)


@route_library_files.route('/library/files/search')
@login_required
def search():
    from arm.automation import tmdb
    query = request.args.get("q", "")[:100]
    kind = request.args.get("kind", "movie")
    try:
        results = tmdb.search_tv(query, limit=8) if kind == "series" else tmdb.search_movies(query, limit=8)
    except Exception as error:  # noqa: BLE001
        return {"results": [], "error": str(error)}
    return {"results": [{"title": r["title"], "year": r["year"], "imdb_id": r.get("imdb_id") or "",
                         "poster_url": (r.get("poster_url") or "").replace("/t/p/original/", "/t/p/w92/")}
                        for r in results]}
