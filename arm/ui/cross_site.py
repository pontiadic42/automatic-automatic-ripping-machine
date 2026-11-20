"""
Refuse requests that change something when they come from another site (upstream issue #1868).

Many actions are plain GET links (/json?mode=delete, /drive/eject/1, ...), so a link or
<img> on any web page could trigger them in a browser that is logged in to A2RM. Browsers
say where a request came from in Sec-Fetch-Site; older ones send Origin or Referer. A
request with none of them (curl, scripts) is let through.

Not covered: /automation/act (phone notification buttons); those links carry their own
signature and are meant to be opened from anywhere.
"""
from urllib.parse import urlsplit

from flask import request

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
# GET routes that change something
CHANGING_PATHS = ("/drive/eject/", "/drive/manual/", "/drive/remove/", "/import_movies", "/logout",
                  "/notificationclose", "/send_movies", "/setup", "/systemdrivescan", "/testapprise",
                  "/updatesysinfo", "/updatetitle")
# /json modes that only read
READ_ONLY_JSON = ("joblist", "dashboard", "getfailed", "getsuccessful", "full", "search", "notify_timeout")
EXEMPT_PATHS = ("/automation/act",)


def changes_something():
    """True when the current request would change state"""
    path = request.path
    if path.startswith(EXEMPT_PATHS):
        return False
    if request.method not in SAFE_METHODS:
        return True
    if path == "/json":
        return str(request.args.get("mode")) not in READ_ONLY_JSON
    return path.startswith(CHANGING_PATHS)


def from_other_site():
    """True when the browser says the request came from a page on another site"""
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site:
        # same-site counts too: another service on the same host or domain (another port)
        return fetch_site not in ("same-origin", "none")
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source or source == "null":
        return bool(source)
    return urlsplit(source).netloc.lower() != request.host.lower()


def check_request():
    """before_request hook: a 403 for a changing request from another site, else None"""
    if changes_something() and from_other_site():
        return ("Blocked: this action was started from another website. "
                "Open A2RM directly and try again.", 403, {"Content-Type": "text/plain; charset=utf-8"})
    return None
