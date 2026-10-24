"""DynamoDB access for the Watches, Sections and Alerts tables."""

import os
import time
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

WATCH_TTL = 30 * 24 * 3600   # idle watches expire after 30 days
ALERT_TTL = 24 * 3600        # unread alerts expire after 24 hours

_db = boto3.resource("dynamodb")
watches = _db.Table(os.environ.get("WATCHES_TABLE", "classpilot-watches"))
sections = _db.Table(os.environ.get("SECTIONS_TABLE", "classpilot-sections"))
alerts = _db.Table(os.environ.get("ALERTS_TABLE", "classpilot-alerts"))


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _query_all(table, **kwargs):
    items, start = [], None
    while True:
        if start:
            kwargs["ExclusiveStartKey"] = start
        page = table.query(**kwargs)
        items += page.get("Items", [])
        start = page.get("LastEvaluatedKey")
        if not start:
            return items


# Watches -------------------------------------------------------------------

def list_watches(watcher_id):
    return _query_all(watches, KeyConditionExpression=Key("watcher_id").eq(watcher_id))


def put_watch(watcher_id, section):
    watches.put_item(Item={
        "watcher_id": watcher_id,
        "class_nbr": section["class_nbr"],
        "term": section["term"],
        "course": section["course"],
        "created_at": now_iso(),
        "last_open": section["open"],
        "expires_at": int(time.time()) + WATCH_TTL,
    })


def delete_watch(watcher_id, class_nbr):
    resp = watches.delete_item(
        Key={"watcher_id": watcher_id, "class_nbr": class_nbr}, ReturnValues="ALL_OLD")
    return "Attributes" in resp


def touch_watches(watcher_id, items):
    """Push expiry forward for an active watcher, at most once a day per watch."""
    soon = int(time.time()) + WATCH_TTL - 24 * 3600
    for w in items:
        if int(w.get("expires_at", 0)) < soon:
            watches.update_item(
                Key={"watcher_id": watcher_id, "class_nbr": w["class_nbr"]},
                UpdateExpression="SET expires_at = :e",
                ExpressionAttributeValues={":e": int(time.time()) + WATCH_TTL})


def scan_watches():
    items, start = [], None
    while True:
        kwargs = {"ProjectionExpression": "watcher_id, class_nbr, term, course"}
        if start:
            kwargs["ExclusiveStartKey"] = start
        page = watches.scan(**kwargs)
        items += page.get("Items", [])
        start = page.get("LastEvaluatedKey")
        if not start:
            return items


# Sections ------------------------------------------------------------------

SECTION_FIELDS = ("course", "title", "section", "days", "start", "end", "instructor",
                  "cap", "open", "total_open", "reserved_open", "withdraw_by")


def get_section(term, class_nbr):
    return sections.get_item(Key={"term": term, "class_nbr": class_nbr}).get("Item")


def put_section(section):
    item = {k: section[k] for k in SECTION_FIELDS if k in section}
    item.update(term=section["term"], class_nbr=section["class_nbr"], checked_at=now_iso())
    sections.put_item(Item=item)


# Alerts --------------------------------------------------------------------

def add_alert(watcher_id, section):
    seen = now_iso()
    alerts.put_item(Item={
        "watcher_id": watcher_id,
        "seen_at": f"{seen}#{section['class_nbr']}",
        "class_nbr": section["class_nbr"],
        "term": section["term"],
        "course": section["course"],
        "open": section["open"],
        "reserved_open": section.get("reserved_open", 0),
        "expires_at": int(time.time()) + ALERT_TTL,
    })


def take_alerts(watcher_id, recent=False):
    """New alerts, marked delivered so a normal check returns each once.

    Delivered alerts stay until their 24-hour TTL, so recent=True can show them
    again if a check-in fetched an alert but never told the student.
    """
    items = _query_all(alerts, KeyConditionExpression=Key("watcher_id").eq(watcher_id))
    now = int(time.time())
    items = [a for a in items if int(a.get("expires_at", now + 1)) > now]
    out = items if recent else [a for a in items if not a.get("delivered_at")]
    stamp = now_iso()
    for a in out:
        if not a.get("delivered_at"):
            alerts.update_item(
                Key={"watcher_id": watcher_id, "seen_at": a["seen_at"]},
                UpdateExpression="SET delivered_at = :d",
                ExpressionAttributeValues={":d": stamp})
    return out
