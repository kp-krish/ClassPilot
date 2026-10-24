"""Fetch and parse seat data from ASU's public class search data endpoint.

The public class search page (catalog.apps.asu.edu) loads its results from this
endpoint and sends the literal header "Authorization: Bearer null" for
logged-out visitors; without it the endpoint answers 401.
"""

import html
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

BASE = "https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search"
HEADERS = {
    "Authorization": "Bearer null",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; ClassPilot seat watcher; +https://github.com/kp-krish/ClassPilot)",
}
TIMEOUT = 8
COURSE_RE = re.compile(r"^\s*([A-Za-z]{2,4})\s*-?\s*(\d{3}[A-Za-z]?)\s*$")


class AsuError(Exception):
    pass


def parse_course(course):
    """'cse230' or 'CSE 230' -> ('CSE', '230')."""
    m = COURSE_RE.match(course or "")
    if not m:
        raise AsuError(f"bad course: {course!r}")
    return m.group(1).upper(), m.group(2).upper()


def _get(path, params, retries=1):
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.load(resp)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == retries:
                raise AsuError(f"ASU request failed: {path}: {e}") from e
            time.sleep(0.5)


def _search(term, **extra):
    params = {
        "refine": "Y", "campusOrOnlineSelection": "A", "honors": "F",
        "promod": "F", "searchType": "all", "term": term, **extra,
    }
    return _get("classes", params).get("classes") or []


def search_course(term, course):
    """All sections of a course in a term, parsed."""
    subject, number = parse_course(course)
    return [parse_section(c) for c in _search(term, subject=subject, catalogNbr=number)]


def find_class(term, class_nbr):
    """One section by its 5-digit class number, or None."""
    for c in _search(term, keywords=str(class_nbr)):
        if c.get("CLAS", {}).get("CLASSNBR") == str(class_nbr):
            return parse_section(c)
    return None


def reserved_open(section):
    """Seats still held for reserved groups (majors, cohorts) in a section."""
    params = {
        "term": section["term"], "classNbr": section["class_nbr"],
        "acadCareer": section["_career"], "crseId": section["_crse_id"],
        "crseOfferNbr": section["_offer_nbr"], "classSection": section["section"],
        "sessionCode": section["session"], "classType": section["_class_type"],
    }
    rows = _get("reservedseatsadditionalinfo", params)
    return sum(max(0, int(r.get("enrlCap") or 0) - int(r.get("enrlTot") or 0)) for r in rows or [])


def with_reserved(section):
    """Split open seats into general and reserved when the section has reserved seats."""
    section["reserved_open"] = 0
    if section["has_reserved"] and section["total_open"] > 0:
        try:
            section["reserved_open"] = min(section["total_open"], reserved_open(section))
        except AsuError as e:
            log.warning("reserved seats lookup failed for %s: %s", section["class_nbr"], e)
    section["open"] = section["total_open"] - section["reserved_open"]
    return section


def _clock(t):
    """'3:00 PM' -> '15:00'."""
    m = re.match(r"(\d{1,2}):(\d{2})\s*([AP]M)", t or "", re.I)
    if not m:
        return ""
    h, mins, ap = int(m.group(1)) % 12, m.group(2), m.group(3).upper()
    return f"{h + (12 if ap == 'PM' else 0):02d}:{mins}"


def _days(daylist):
    text = re.sub(r"<[^>]+>", " ", html.unescape(daylist or "")).replace("\xa0", " ")
    return " ".join(text.split())


def _date(value):
    return (value or "")[:10]


def parse_section(raw):
    c = raw.get("CLAS", {})
    seats = raw.get("seatInfo") or {}
    cap = int(seats.get("ENRL_CAP", c.get("ENRLCAP") or 0) or 0)
    tot = int(seats.get("ENRL_TOT", c.get("ENRLTOT") or 0) or 0)
    starts = [s for s in (c.get("STARTTIMES") or []) if s]
    ends = [s for s in (c.get("ENDTIMES") or []) if s]
    return {
        "term": c.get("STRM"),
        "class_nbr": c.get("CLASSNBR"),
        "course": f"{c.get('SUBJECT')} {c.get('CATALOGNBR')}",
        "title": c.get("TITLE"),
        "section": c.get("CLASSSECTION"),
        "session": c.get("SESSIONCODE"),
        "mode": c.get("INSTRUCTIONMODE"),
        "campus": c.get("CAMPUS"),
        "days": _days(c.get("DAYLIST")),
        "start": _clock(starts[0]) if starts else "",
        "end": _clock(ends[0]) if ends else "",
        "instructor": ", ".join(c.get("INSTRUCTORSLIST") or []) or "Staff",
        "cap": cap,
        "total_open": max(0, cap - tot),
        "open": max(0, cap - tot),
        "has_reserved": c.get("HASACTIVERESERVEDSEATS") == "Y",
        # DROPWITHPENALTY is the course withdrawal (W) deadline: Fall 2026 session C
        # reads 2026-11-04, matching the registrar calendar. WITHDRAWWITHPENALTY is
        # the complete session withdrawal deadline (the last day of classes).
        "withdraw_by": _date(c.get("DROPWITHPENALTY")),
        "complete_withdraw_by": _date(c.get("WITHDRAWWITHPENALTY")),
        "_career": c.get("ACADCAREER"),
        "_crse_id": c.get("CRSEID"),
        "_offer_nbr": c.get("CRSEOFFERNBR"),
        "_class_type": c.get("CLASSTYPE"),
    }


def public(section):
    """Drop internal fields before returning a section to a client."""
    return {k: v for k, v in section.items() if not k.startswith("_")}
