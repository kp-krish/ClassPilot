#!/usr/bin/env python3
"""ClassPilot seats: ASU section lookups and the ClassPilot seat watcher.

Python standard library only. Section lookups and deadlines go straight to ASU's
public class search; watching goes through the ClassPilot Seat API, which only
ever receives a random watcher ID, a term and class numbers.

  python3 scripts/seats.py init
  python3 scripts/seats.py sections --term "Spring 2027" --course "CSE 230"
  python3 scripts/seats.py deadline --term "Fall 2026" --course "CSE 230" [--class 60357]
  python3 scripts/seats.py watch --term "Spring 2027" --class 27345
  python3 scripts/seats.py unwatch --class 27345
  python3 scripts/seats.py watches
  python3 scripts/seats.py alerts
  Add --sample to any command to use assets/sample_sections.json and no network.

Exit codes: 0 ok, 5 seat service unreachable, 6 class not found,
7 watch limit reached, 8 bad input, 9 ASU class search unreachable.
"""

import argparse
import html
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = os.environ.get("CLASSPILOT_API", "https://qhfrx4auj4.execute-api.us-east-2.amazonaws.com")
ASU = "https://eadvs-cscc-catalog-api.apps.asu.edu/catalog-microservices/api/v1/search"
ASU_HEADERS = {"Authorization": "Bearer null", "Accept": "application/json",
               "User-Agent": "Mozilla/5.0 (compatible; ClassPilot)"}
# The deep links ASU's own class search uses; the student signs in and confirms there.
ENROLL_URL = "https://www.asu.edu/go/addclass?STRM={term}"
SWAP_URL = "https://www.asu.edu/go/swapclass?STRM={term}"
SAMPLE = Path(__file__).resolve().parent.parent / "assets" / "sample_sections.json"

OK, API_DOWN, NOT_FOUND, LIMIT, BAD_INPUT, ASU_DOWN = 0, 5, 6, 7, 8, 9
SEASONS = {"spring": "1", "summer": "4", "fall": "7"}
MODES = {"P": "in person", "HY": "hybrid", "OL": "online", "ICOL": "iCourse", "SY": "live online"}


class Fail(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# State ---------------------------------------------------------------------

def state_dir():
    base = os.environ.get("SKILL_DATA_DIR") or os.path.join(os.path.expanduser("~"), ".config", "classpilot")
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_watcher():
    f = state_dir() / "watcher.json"
    if f.exists():
        try:
            return json.loads(f.read_text())["watcher_id"]
        except (ValueError, KeyError):
            pass
    return None


def save_watcher(watcher_id):
    (state_dir() / "watcher.json").write_text(json.dumps({"watcher_id": watcher_id}))


def require_watcher():
    w = load_watcher()
    if not w:
        w = init_watcher()
    return w


# HTTP ----------------------------------------------------------------------

def _request(url, method="GET", body=None, headers=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Accept": "application/json", **(headers or {})}
    if data:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def api(method, path, body=None, **query):
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
    for attempt in range(2):
        try:
            return _request(url, method, body)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("error", str(e))
            except ValueError:
                msg = str(e)
            code = {404: NOT_FOUND, 429: LIMIT, 400: BAD_INPUT}.get(e.code)
            if code:
                raise Fail(code, msg)
            if attempt:
                raise Fail(API_DOWN, f"seat service error {e.code}")
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if attempt:
                raise Fail(API_DOWN, f"seat service unreachable: {e}")
        time.sleep(1)


def asu(path, **params):
    url = f"{ASU}/{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(2):
        try:
            return _request(url, headers=ASU_HEADERS)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429) or attempt:  # blocked: retrying won't help
                raise Fail(ASU_DOWN, f"ASU class search refused the request ({e.code})")
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if attempt:
                raise Fail(ASU_DOWN, f"ASU class search unreachable: {e}")
            time.sleep(1)


# ASU parsing ---------------------------------------------------------------

def term_code(term):
    text = str(term or "").strip().lower()
    if re.fullmatch(r"2\d{2}[147]", text):
        return text
    season = next((s for s in SEASONS if s in text), None)
    year = re.search(r"\b(20\d{2}|\d{2})\b", text)
    if not season or not year:
        raise Fail(BAD_INPUT, f'term should look like "Spring 2027", got {term!r}')
    return f"2{year.group(1)[-2:]}{SEASONS[season]}"


def term_label(code):
    season = {v: k.capitalize() for k, v in SEASONS.items()}[code[3]]
    return f"{season} 20{code[1:3]}"


def parse_course(course):
    m = re.match(r"^\s*([A-Za-z]{2,4})\s*-?\s*(\d{3}[A-Za-z]?)\s*$", course or "")
    if not m:
        raise Fail(BAD_INPUT, f'course should look like "CSE 230", got {course!r}')
    return m.group(1).upper(), m.group(2).upper()


def _clock(t):
    m = re.match(r"(\d{1,2}):(\d{2})\s*([AP]M)", t or "", re.I)
    if not m:
        return ""
    h = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
    return f"{h:02d}:{m.group(2)}"


def parse_section(raw):
    c = raw.get("CLAS", {})
    seats = raw.get("seatInfo") or {}
    cap = int(seats.get("ENRL_CAP", c.get("ENRLCAP") or 0) or 0)
    tot = int(seats.get("ENRL_TOT", c.get("ENRLTOT") or 0) or 0)
    days = re.sub(r"<[^>]+>", " ", html.unescape(c.get("DAYLIST") or "")).replace("\xa0", " ")
    starts = [s for s in (c.get("STARTTIMES") or []) if s]
    ends = [s for s in (c.get("ENDTIMES") or []) if s]
    return {
        "raw": c, "class_nbr": c.get("CLASSNBR"), "course": f"{c.get('SUBJECT')} {c.get('CATALOGNBR')}",
        "session": c.get("SESSIONCODE"), "mode": MODES.get(c.get("INSTRUCTIONMODE"), c.get("INSTRUCTIONMODE")),
        "days": " ".join(days.split()), "start": _clock(starts[0]) if starts else "",
        "end": _clock(ends[0]) if ends else "",
        "instructor": ", ".join(c.get("INSTRUCTORSLIST") or []) or "Staff",
        "cap": cap, "open": max(0, cap - tot), "reserved": 0,
        "has_reserved": c.get("HASACTIVERESERVEDSEATS") == "Y",
        "withdraw_by": (c.get("DROPWITHPENALTY") or "")[:10],
        "complete_withdraw_by": (c.get("WITHDRAWWITHPENALTY") or "")[:10],
        "where": _where(c),
        "dates": f"{_short_date(c.get('STARTDATE'))}-{_short_date(c.get('ENDDATE'))}",
    }


def _where(c):
    """'Poly AGBC154', 'Tempe BYAC110' or 'ASU Online (ASU Online students only)'."""
    try:
        names = [b.get("NAME", "") for b in json.loads(c.get("LOCATIONBUILDING") or "[]")]
    except ValueError:
        names = []
    if c.get("LOCATION") == "ASUONLINE":
        return "ASU Online (ASU Online students only)"
    rooms = [n.replace(" - ", " ") for n in names if n and not n.startswith("Internet")]
    return rooms[0] if rooms else (c.get("LOCATION") or "").title()


def _short_date(value):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    if not m:
        return "?"
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{months[int(m.group(2)) - 1]} {int(m.group(3))}"


def fetch_sections(term, course, sample=False):
    subject, number = parse_course(course)
    if sample:
        rows = json.loads(SAMPLE.read_text())["classes"]
    else:
        try:
            rows = asu("classes", refine="Y", campusOrOnlineSelection="A", honors="F", promod="F",
                       searchType="all", subject=subject, catalogNbr=number, term=term).get("classes") or []
        except Fail as direct:
            # ASU blocks some cloud networks; the seat service fetches the same data from AWS.
            try:
                return [from_api(r) for r in api("GET", "/sections", term=term, course=f"{subject} {number}")]
            except Fail:
                raise Fail(ASU_DOWN, f"{direct}; the seat service could not reach ASU either")
    sections = [parse_section(r) for r in rows]
    for s in sections:
        if s["has_reserved"] and s["open"] > 0:
            s["reserved"] = min(s["open"], reserved_open(s, sample))
            s["open"] -= s["reserved"]
    return sections


def from_api(r):
    """A section from the Seat API's GET /sections, in parse_section's shape."""
    return {
        "raw": {}, "class_nbr": r["class_nbr"], "course": r["course"], "session": r.get("session"),
        "mode": MODES.get(r.get("mode"), r.get("mode")), "days": r.get("days", ""),
        "start": r.get("start", ""), "end": r.get("end", ""), "instructor": r.get("instructor", "Staff"),
        "cap": r.get("cap", 0), "open": r.get("open", 0), "reserved": r.get("reserved_open", 0),
        "has_reserved": r.get("has_reserved", False), "withdraw_by": r.get("withdraw_by", ""),
        "complete_withdraw_by": r.get("complete_withdraw_by", ""),
        "where": r.get("where", ""), "dates": r.get("dates", ""),
    }


def reserved_open(s, sample=False):
    if sample:
        return int(s["raw"].get("SAMPLE_RESERVED_OPEN", 0))
    c = s["raw"]
    try:
        rows = asu("reservedseatsadditionalinfo", term=c["STRM"], classNbr=c["CLASSNBR"],
                   acadCareer=c["ACADCAREER"], crseId=c["CRSEID"], crseOfferNbr=c["CRSEOFFERNBR"],
                   classSection=c["CLASSSECTION"], sessionCode=c["SESSIONCODE"], classType=c["CLASSTYPE"])
    except Fail:
        return 0
    return sum(max(0, int(r.get("enrlCap") or 0) - int(r.get("enrlTot") or 0)) for r in rows or [])


def _when(s):
    if not s["days"]:
        return s["mode"]
    return f"{s['days']} {s['start']}-{s['end']}" + (f" ({s['mode']})" if s["mode"] != "in person" else "")


# Commands ------------------------------------------------------------------

def init_watcher():
    try:
        watcher_id = api("POST", "/watchers")["watcher_id"]
    except Fail:
        watcher_id = "w_" + secrets.token_urlsafe(16)  # same format the API issues
    save_watcher(watcher_id)
    return watcher_id


def cmd_init(args):
    if args.sample:
        print("Watcher ready (sample): w_sample0000000000000000")
        return OK
    existing = load_watcher()
    print(f"Watcher ready: {existing or init_watcher()}")
    return OK


def cmd_sections(args):
    term = term_code(args.term)
    sections = fetch_sections(term, args.course, args.sample)
    if not sections:
        raise Fail(NOT_FOUND, f"no sections of {args.course} found for {term_label(term)}")
    print(f"{sections[0]['course']}, {term_label(term)}: {len(sections)} sections")
    for s in sorted(sections, key=lambda s: (-s["open"], s["class_nbr"])):
        if s["open"]:
            seats = f"{s['open']} open of {s['cap']}"
            note = f", {s['reserved']} more reserved for certain majors" if s["reserved"] else ""
        elif s["reserved"]:
            seats, note = f"no open seats for everyone; {s['reserved']} reserved for certain majors", ""
        else:
            seats, note = f"FULL ({s['cap']} seats)", ""
        print(f"- #{s['class_nbr']} {_when(s)}, {s['where']}, session {s['session']} ({s['dates']}), "
              f"{s['instructor']}: {seats}{note}")
    return OK


def cmd_deadline(args):
    term = term_code(args.term)
    sections = fetch_sections(term, args.course, args.sample)
    if args.class_nbr:
        sections = [s for s in sections if s["class_nbr"] == args.class_nbr]
    if not sections:
        raise Fail(NOT_FOUND, f"class not found in {term_label(term)}")
    seen = {}
    for s in sections:
        seen.setdefault((s["session"], s["withdraw_by"], s["complete_withdraw_by"]), []).append(s["class_nbr"])
    for (session, w, cw), nbrs in sorted(seen.items()):
        print(f"{sections[0]['course']} session {session} (#{', #'.join(nbrs[:3])}): "
              f"course withdrawal by {w}; complete withdrawal by {cw}")
    return OK


def cmd_watch(args):
    term = term_code(args.term)
    if args.sample:
        s = next((x for x in fetch_sections(term, "CSE 230", True) if x["class_nbr"] == args.class_nbr), None)
        if not s:
            raise Fail(NOT_FOUND, f"class {args.class_nbr} not found")
        print(f"Watching {s['course']} #{s['class_nbr']} ({term_label(term)}), {s['open']} open now (sample)")
        return OK
    r = api("POST", "/watches", {"watcher_id": require_watcher(), "term": term, "class_nbr": args.class_nbr})
    days = f"{r['days']} {r['start']}-{r['end']}" if r.get("days") else MODES.get(r.get("mode"), "")
    status = f"{r['open']} open right now" if r["open"] else "full right now"
    print(f"Watching {r['course']} #{r['class_nbr']} ({term_label(term)}, {days}): {status}")
    return OK


def cmd_unwatch(args):
    if not args.sample:
        api("DELETE", f"/watches/{args.class_nbr}", watcher_id=require_watcher())
    print(f"Stopped watching #{args.class_nbr}")
    return OK


def cmd_watches(args):
    rows = [] if args.sample else api("GET", "/watches", watcher_id=require_watcher())
    if not rows:
        print("Not watching any classes")
        return OK
    for r in rows:
        status = f"{r['open']} open" if r.get("open") else "full"
        print(f"- {r['course']} #{r['class_nbr']} ({r['term']}): {status}")
    return OK


def _ago(seen_at):
    """' (seen 12 min ago)' from an ISO UTC time, or ''."""
    try:
        t = time.mktime(time.strptime(seen_at, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except (TypeError, ValueError):
        return ""
    mins = max(0, int((time.time() - t) // 60))
    return " (seen just now)" if mins < 2 else f" (seen {mins} min ago)" if mins < 120 else f" (seen {mins // 60} h ago)"


def cmd_alerts(args):
    if args.sample:
        rows = [{"course": "CSE 230", "class_nbr": "27345", "term": "Spring 2027", "open": 2, "reserved_open": 0,
                 "seen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 300))}]
    elif args.recent:
        rows = api("GET", "/alerts", watcher_id=require_watcher(), recent="1")
    else:
        rows = api("GET", "/alerts", watcher_id=require_watcher())
    if not rows:
        print("No new seat openings in the last 24 hours." if args.recent
              else "No new seat openings. Send the student nothing.")
        return OK
    # Worded as an instruction: a scheduled check-in reads this with no other context.
    print("ACTION: text the student this message now, then wait for their yes before opening a link.")
    print("Each alert is a fresh opening: the seat service saw this section full, then open again,")
    print("so send it even if the section had open seats earlier.")
    print()
    for r in rows:
        extra = f" ({r['reserved_open']} more reserved for certain majors)" if r.get("reserved_open") else ""
        print(f"Seat open: {r['course']} #{r['class_nbr']} ({r['term']}) was full and now has "
              f"{r['open']} open{extra}{_ago(r.get('seen_at'))}. Seats go fast. Open enrollment?")
    print()
    for t in sorted({r["term"] for r in rows}):
        code = term_code(t)
        nbrs = " or ".join(r["class_nbr"] for r in rows if r["term"] == t)
        print(f"If yes, open in the browser and hand it to the student ({t}):")
        print(f"Add class: {ENROLL_URL.format(term=code)}")
        print(f"Swap class: {SWAP_URL.format(term=code)}")
        # ASU's page opens with an empty class number box.
        print(f"With the link, text: Type {nbrs} in the class number box and tap Next.")
    return OK


def main(argv=None):
    p = argparse.ArgumentParser(description="ClassPilot seats")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, *opts):
        sp = sub.add_parser(name)
        sp.add_argument("--sample", action="store_true", help="bundled data, no network or saved state")
        for o in opts:
            o(sp)
        sp.set_defaults(fn=fn)

    term = lambda sp: sp.add_argument("--term", required=True, help='e.g. "Spring 2027"')
    course = lambda sp: sp.add_argument("--course", required=True, help='e.g. "CSE 230"')
    klass = lambda sp: sp.add_argument("--class", dest="class_nbr", required=True, help="5-digit class number")
    opt_class = lambda sp: sp.add_argument("--class", dest="class_nbr", help="5-digit class number")

    add("init", cmd_init)
    add("sections", cmd_sections, term, course)
    add("deadline", cmd_deadline, term, course, opt_class)
    add("watch", cmd_watch, term, klass)
    add("unwatch", cmd_unwatch, klass)
    add("watches", cmd_watches)
    add("alerts", cmd_alerts, lambda sp: sp.add_argument(
        "--recent", action="store_true", help="every opening from the last 24 hours, even ones already fetched"))

    args = p.parse_args(argv)
    if getattr(args, "class_nbr", None) and not re.fullmatch(r"\d{5}", args.class_nbr):
        print("error: class number must be 5 digits")
        return BAD_INPUT
    try:
        return args.fn(args)
    except Fail as e:
        print(f"error: {e}")
        return e.code


if __name__ == "__main__":
    sys.exit(main())
