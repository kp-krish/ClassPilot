"""Seat API: one Lambda behind an API Gateway HTTP API (payload v2).

The only identifier is a random watcher ID; no names, emails or grades arrive here.
"""

import base64
import json
import logging
import re
import secrets
from decimal import Decimal

import asu_seats
import store
import terms

log = logging.getLogger()
log.setLevel(logging.INFO)

MAX_WATCHES = 10
WATCHER_RE = re.compile(r"^w_[A-Za-z0-9_-]{16,40}$")
CLASS_RE = re.compile(r"^\d{5}$")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _json(value):
    if isinstance(value, Decimal):
        return int(value) if value == int(value) else float(value)
    raise TypeError(f"not JSON serializable: {type(value)}")


def respond(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json),
    }


def _watcher(value):
    if not WATCHER_RE.match(value or ""):
        raise ApiError(400, "missing or bad watcher_id")
    return value


def _class_nbr(value):
    value = str(value or "").strip()
    if not CLASS_RE.match(value):
        raise ApiError(400, "class number must be 5 digits")
    return value


def _term(value):
    try:
        return terms.term_code(value)
    except terms.TermError as e:
        raise ApiError(400, str(e))


def _asu(fn, *args):
    try:
        return fn(*args)
    except asu_seats.AsuError as e:
        log.warning("ASU error: %s", e)
        raise ApiError(502, "ASU class search is not responding")


# Routes --------------------------------------------------------------------

def create_watcher(event):
    return 201, {"watcher_id": "w_" + secrets.token_urlsafe(16)}


def get_sections(event):
    q = event.get("queryStringParameters") or {}
    term = _term(q.get("term"))
    try:
        asu_seats.parse_course(q.get("course"))
    except asu_seats.AsuError:
        raise ApiError(400, "course must look like CSE 230")
    found = _asu(asu_seats.search_course, term, q["course"])
    return 200, [asu_seats.public(asu_seats.with_reserved(s)) for s in found]


def _body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):  # API Gateway encodes non-JSON content types
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    return json.loads(raw)


def add_watch(event):
    try:
        body = _body(event)
    except (json.JSONDecodeError, ValueError):
        raise ApiError(400, "body must be JSON")
    watcher_id = _watcher(body.get("watcher_id"))
    term = _term(body.get("term"))
    class_nbr = _class_nbr(body.get("class_nbr"))

    current = store.list_watches(watcher_id)
    if class_nbr not in {w["class_nbr"] for w in current} and len(current) >= MAX_WATCHES:
        raise ApiError(429, f"limit of {MAX_WATCHES} watches reached")

    section = _asu(asu_seats.find_class, term, class_nbr)
    if not section:
        raise ApiError(404, f"class {class_nbr} not found in {terms.term_label(term)}")
    asu_seats.with_reserved(section)
    store.put_watch(watcher_id, section)
    store.put_section(section)  # baseline, so the poller alerts only on a later 0 -> open
    return 200, {"ok": True, **asu_seats.public(section)}


def remove_watch(event):
    q = event.get("queryStringParameters") or {}
    watcher_id = _watcher(q.get("watcher_id"))
    class_nbr = _class_nbr((event.get("pathParameters") or {}).get("class_nbr"))
    if not store.delete_watch(watcher_id, class_nbr):
        raise ApiError(404, f"not watching {class_nbr}")
    return 200, {"ok": True}


def list_watches(event):
    q = event.get("queryStringParameters") or {}
    watcher_id = _watcher(q.get("watcher_id"))
    items = store.list_watches(watcher_id)
    store.touch_watches(watcher_id, items)
    out = []
    for w in items:
        latest = store.get_section(w["term"], w["class_nbr"]) or {}
        out.append({
            "class_nbr": w["class_nbr"], "term": terms.term_label(w["term"]),
            "course": w["course"], "open": latest.get("open", w.get("last_open")),
            "days": latest.get("days", ""), "start": latest.get("start", ""),
            "end": latest.get("end", ""), "checked_at": latest.get("checked_at"),
        })
    return 200, out


def get_alerts(event):
    q = event.get("queryStringParameters") or {}
    watcher_id = _watcher(q.get("watcher_id"))
    store.touch_watches(watcher_id, store.list_watches(watcher_id))
    out = []
    recent = q.get("recent") in ("1", "true", "yes")
    for a in store.take_alerts(watcher_id, recent):
        out.append({
            "class_nbr": a["class_nbr"], "term": terms.term_label(a["term"]),
            "course": a["course"], "open": a["open"],
            "reserved_open": a.get("reserved_open", 0),
            "seen_at": a["seen_at"].split("#")[0],
            "delivered_before": bool(a.get("delivered_at")) and recent,
        })
    return 200, out


ROUTES = {
    "POST /watchers": create_watcher,
    "GET /sections": get_sections,
    "POST /watches": add_watch,
    "DELETE /watches/{class_nbr}": remove_watch,
    "GET /watches": list_watches,
    "GET /alerts": get_alerts,
}


def handler(event, context):
    log.info("route %s", event.get("routeKey"))
    route = ROUTES.get(event.get("routeKey"))
    if not route:
        return respond(404, {"error": "no such route"})
    try:
        status, body = route(event)
        return respond(status, body)
    except ApiError as e:
        return respond(e.status, {"error": str(e)})
    except Exception:
        log.exception("unhandled error on %s", event.get("routeKey"))
        return respond(500, {"error": "internal error"})
