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

from arm.automation import library

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
                     sum(1 for item in result["items"] if item["issues"]) if name == "attention" else
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


def _form_roles():
    roles = {}
    for key, value in request.form.items():
        if key.startswith("file_"):
            roles[value] = request.form.get("role_" + key[5:], "")
    return roles


@route_library_files.route('/library/files/item', methods=['GET', 'POST'])
@login_required
def item():
    item_id = request.values.get("id", "")
    session["page_title"] = "Manage files"
    try:
        found = library.find(item_id)
    except library.LibraryError as error:
        flash(str(error), "danger")
        return redirect(url_for("route_library_files.files"))
    values, roles, preview = library.defaults(found), None, None
    if request.method == "POST":
        action = request.form.get("action")
        values, roles = _form_values(), _form_roles()
        try:
            if action in ("apply", "trash") and _active_job():
                raise library.LibraryError("A disc is ripping or transcoding; try again when it's done.")
            if action == "trash":
                library.trash(found["id"])
                flash(f"Moved {found['id']} to the trash. Undo it from the list of changes.", "success")
                return redirect(url_for("route_library_files.files"))
            if action == "apply":
                entry = library.apply(found["id"], values, roles)
                flash(f"Done: {entry['summary']} ({len(entry['moves'])} file(s) moved"
                      + (f", {len(entry['left'])} already there left in place" if entry["left"] else "")
                      + "). Undo it from the list of changes.", "success")
                return redirect(url_for("route_library_files.files"))
            preview = library.plan(found["id"], values, roles)
            roles = preview["roles"]
        except library.LibraryError as error:
            flash(str(error), "danger")
    roles = roles or library.default_roles(found["id"], values.get("kind") or "movie")
    base = library.absolute(found["id"])
    base = base if os.path.isdir(base) else os.path.dirname(base)
    file_list = [{"rel": rel, "role": role, "size": size_text(max(0, library._size(os.path.join(base, rel))))}
                 for rel, role in sorted(roles.items(), key=lambda pair: library._episode_order(pair[0]))]
    group = library.title_key(found["title"], found["year"] if found["root"] == "movies" else "")
    others = [other for other in (library.store.load_state().get("library") or {}).get("items", [])
              if other.get("group") == group and other["id"] != found["id"]]
    return render_template("library_item.html", item=found, v=values, files=file_list, preview=preview,
                           others=others, size_text=size_text, roles=library.ROLES)


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
