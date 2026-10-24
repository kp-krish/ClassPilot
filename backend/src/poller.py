"""Seat poller: runs every minute from EventBridge Scheduler.

Each watched course is fetched from ASU once per run, however many students
watch it. When a watched section's open seats go from 0 to more than 0, one
alert is written per watcher of that section.
"""

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import asu_seats
import store

log = logging.getLogger()
log.setLevel(logging.INFO)

WORKERS = 8


def check_course(term, course, watched):
    """watched: {class_nbr: [watcher_id, ...]}. Returns (alerts written, sections checked)."""
    found = {s["class_nbr"]: s for s in asu_seats.search_course(term, course)}
    written = checked = 0
    for class_nbr, watcher_ids in watched.items():
        section = found.get(class_nbr)
        if not section:
            log.warning("class %s not in ASU results for %s %s", class_nbr, term, course)
            continue
        asu_seats.with_reserved(section)
        before = store.get_section(term, class_nbr)
        store.put_section(section)
        checked += 1
        if before is not None and int(before.get("open", 0)) == 0 and section["open"] > 0:
            for watcher_id in watcher_ids:
                store.add_alert(watcher_id, section)
                written += 1
            log.info("OPENING %s %s: %d open, %d watchers",
                     course, class_nbr, section["open"], len(watcher_ids))
    return written, checked


def handler(event, context):
    groups = defaultdict(lambda: defaultdict(list))
    for w in store.scan_watches():
        groups[(w["term"], w["course"])][w["class_nbr"]].append(w["watcher_id"])

    alerts = checked = failed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(check_course, t, c, watched): (t, c)
                   for (t, c), watched in groups.items()}
        for fut, (term, course) in futures.items():
            try:
                a, n = fut.result()
                alerts += a
                checked += n
            except Exception as e:  # skip until the next run
                failed += 1
                log.warning("poll failed for %s %s: %s", term, course, e)

    summary = {"courses": len(groups), "sections": checked, "alerts": alerts, "failed": failed}
    log.info("poll %s", summary)
    if groups and failed == len(groups):
        raise RuntimeError(f"every course fetch failed: {summary}")  # trips the error alarm
    return summary
