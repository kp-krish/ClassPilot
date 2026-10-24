import copy
import json

import pytest

import asu_seats
import terms

WATCHER = "w_" + "a" * 22


def call(api, route, body=None, query=None, path=None):
    resp = api.handler({"routeKey": route, "body": json.dumps(body) if body else None,
                        "queryStringParameters": query, "pathParameters": path}, None)
    return resp["statusCode"], json.loads(resp["body"])


def set_enrolled(state, class_nbr, enrolled):
    state["classes"] = copy.deepcopy(state["classes"])
    for c in state["classes"]:
        if c["CLAS"]["CLASSNBR"] == class_nbr:
            c["seatInfo"]["ENRL_TOT"] = enrolled


# terms ---------------------------------------------------------------------

@pytest.mark.parametrize("text,code", [
    ("Spring 2027", "2271"), ("fall 26", "2267"), ("Summer 2026", "2264"), ("2271", "2271")])
def test_term_code(text, code):
    assert terms.term_code(text) == code


def test_term_label_and_errors():
    assert terms.term_label("2267") == "Fall 2026"
    with pytest.raises(terms.TermError):
        terms.term_code("Winter 2027")


# parsing -------------------------------------------------------------------

def test_parse_section(asu_classes):
    by_nbr = {s["class_nbr"]: s for s in map(asu_seats.parse_section, asu_classes)}
    s = by_nbr["15951"]
    assert (s["course"], s["cap"], s["total_open"]) == ("CSE 230", 50, 41)
    assert (s["days"], s["start"], s["end"]) == ("M W", "15:00", "16:15")
    assert s["has_reserved"] is True
    assert by_nbr["27345"]["days"] == "T"


def test_reserved_split(asu_classes, monkeypatch):
    s = asu_seats.parse_section(next(c for c in asu_classes if c["CLAS"]["CLASSNBR"] == "15951"))
    monkeypatch.setattr(asu_seats, "reserved_open", lambda _: 22)
    asu_seats.with_reserved(s)
    assert (s["open"], s["reserved_open"]) == (19, 22)


@pytest.mark.parametrize("course", ["CSE 230", "cse230", "CSE-230", "mat 265"])
def test_parse_course(course):
    assert asu_seats.parse_course(course)[1] in ("230", "265")


# API -----------------------------------------------------------------------

def test_watch_flow_and_alerts(aws):
    import api
    import poller

    status, body = call(api, "POST /watchers")
    assert status == 201 and body["watcher_id"].startswith("w_")

    set_enrolled(aws, "27345", 80)  # full
    status, body = call(api, "POST /watches", {"watcher_id": WATCHER, "term": "Spring 2027", "class_nbr": "27345"})
    assert status == 200 and body["open"] == 0 and body["course"] == "CSE 230"

    # still full: no alert
    assert poller.handler({}, None)["alerts"] == 0
    assert call(api, "GET /alerts", query={"watcher_id": WATCHER}) == (200, [])

    # a seat opens: one alert, delivered once
    set_enrolled(aws, "27345", 78)
    assert poller.handler({}, None)["alerts"] == 1
    status, alerts = call(api, "GET /alerts", query={"watcher_id": WATCHER})
    assert status == 200 and [(a["class_nbr"], a["open"]) for a in alerts] == [("27345", 2)]
    assert call(api, "GET /alerts", query={"watcher_id": WATCHER})[1] == []

    # stays open: no repeat alert
    assert poller.handler({}, None)["alerts"] == 0

    status, watches = call(api, "GET /watches", query={"watcher_id": WATCHER})
    assert watches[0]["open"] == 2 and watches[0]["term"] == "Spring 2027"

    assert call(api, "DELETE /watches/{class_nbr}", query={"watcher_id": WATCHER}, path={"class_nbr": "27345"})[0] == 200
    assert call(api, "DELETE /watches/{class_nbr}", query={"watcher_id": WATCHER}, path={"class_nbr": "27345"})[0] == 404


def test_poller_fetches_each_course_once(aws):
    import api
    import poller
    for i, nbr in enumerate(["27345", "27346", "15951"]):
        call(api, "POST /watches", {"watcher_id": f"w_{i}" + "b" * 21, "term": "2271", "class_nbr": nbr})
    aws["calls"] = 0
    summary = poller.handler({}, None)
    assert summary["courses"] == 1 and summary["sections"] == 3 and aws["calls"] == 1


def test_reserved_seats_do_not_count_as_open(aws):
    import api
    import poller
    set_enrolled(aws, "15951", 50)
    call(api, "POST /watches", {"watcher_id": WATCHER, "term": "2271", "class_nbr": "15951"})
    aws["reserved"] = 3
    set_enrolled(aws, "15951", 47)  # 3 seats free, all reserved
    assert poller.handler({}, None)["alerts"] == 0


@pytest.mark.parametrize("body,status", [
    ({"watcher_id": "nope", "term": "2271", "class_nbr": "27345"}, 400),
    ({"watcher_id": WATCHER, "term": "Winter 2027", "class_nbr": "27345"}, 400),
    ({"watcher_id": WATCHER, "term": "2271", "class_nbr": "123"}, 400),
    ({"watcher_id": WATCHER, "term": "2271", "class_nbr": "99999"}, 404),
])
def test_watch_validation(aws, body, status):
    import api
    assert call(api, "POST /watches", body)[0] == status


def test_watch_cap(aws, asu_classes):
    import api
    import store
    for i in range(10):
        store.watches.put_item(Item={"watcher_id": WATCHER, "class_nbr": f"1000{i}",
                                     "term": "2271", "course": "CSE 230", "expires_at": 9999999999})
    status, body = call(api, "POST /watches", {"watcher_id": WATCHER, "term": "2271", "class_nbr": "27345"})
    assert status == 429 and "limit" in body["error"]


def test_sections_and_unknown_route(aws):
    import api
    status, rows = call(api, "GET /sections", query={"term": "Spring 2027", "course": "CSE 230"})
    assert status == 200 and len(rows) == 5 and not any(k.startswith("_") for k in rows[0])
    assert call(api, "GET /sections", query={"term": "2271", "course": "x"})[0] == 400
    assert call(api, "GET /nope")[0] == 404
