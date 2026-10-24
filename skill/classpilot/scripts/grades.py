#!/usr/bin/env python3
"""ClassPilot grade math: what a student needs on the rest of a course.

Runs locally with the Python standard library and sends nothing anywhere.

  python3 scripts/grades.py --input grades.json --today 2026-10-24
  python3 scripts/grades.py --sample

Input JSON (Cubbie writes it from Canvas and the syllabus):
  course        "CSE 230"
  scale         {"A": 90, "B": 80, "C": 70, "D": 60}   lowest percent for each letter
  categories    [{"name", "weight", "earned", "left"}]
                  weight  share of the final grade (0.25 or 25)
                  earned  average score on the graded part of this category (0.70 or 70)
                  left    share of the final grade still ungraded in this category
                  scores  optional [[earned, possible], ...]; replaces "earned"
                  drop_lowest  optional number of lowest scores to drop (needs scores)
  drop_lowest   optional list of category names that drop their single lowest score
  extra_credit  optional percentage points added to the final grade
  deadline      optional withdrawal deadline, YYYY-MM-DD
  today         optional student's local date, YYYY-MM-DD (or pass --today)

Exit codes: 0 ok, 2 missing weights, 3 weights do not sum to 100%, 4 bad input file.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

OK, MISSING_WEIGHTS, BAD_SUM, BAD_INPUT = 0, 2, 3, 4
SAMPLE = Path(__file__).resolve().parent.parent / "assets" / "sample_grades.json"
DEFAULT_SCALE = {"A": 90, "B": 80, "C": 70, "D": 60}


class InputError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fraction(value, field, name):
    """Accept 0.7 or 70 and return 0.7."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise InputError(BAD_INPUT, f"{name}: {field} must be a number")
    if v < 0:
        raise InputError(BAD_INPUT, f"{name}: {field} cannot be negative")
    return v / 100 if v > 1.5 else v


def _earned_from_scores(cat, drop):
    scores = []
    for pair in cat["scores"]:
        try:
            got, out_of = float(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            raise InputError(BAD_INPUT, f"{cat.get('name')}: scores must be [earned, possible] pairs")
        if out_of > 0:
            scores.append((got, out_of))
    scores.sort(key=lambda s: s[0] / s[1])
    kept = scores[drop:] if drop < len(scores) else scores[-1:]
    if not kept:
        return None
    return sum(s[0] for s in kept) / sum(s[1] for s in kept)


def load_categories(data):
    raw = data.get("categories")
    if not raw or not isinstance(raw, list):
        raise InputError(MISSING_WEIGHTS, "no grade categories or weights given")
    if any(not isinstance(c, dict) or c.get("weight") in (None, "") for c in raw):
        raise InputError(MISSING_WEIGHTS, "a category is missing its weight")

    weights = [float(c["weight"]) for c in raw]
    as_percent = sum(weights) > 1.5
    drop_named = {str(n).lower() for n in data.get("drop_lowest") or []}

    cats = []
    for c in raw:
        name = str(c.get("name") or "Category")
        weight = float(c["weight"]) / (100 if as_percent else 1)
        left = float(c.get("left") or 0) / (100 if as_percent else 1)
        if left > weight + 1e-9:
            raise InputError(BAD_INPUT, f"{name}: left ({left:.0%}) is more than its weight ({weight:.0%})")
        drop = int(c.get("drop_lowest") or (1 if name.lower() in drop_named else 0))
        earned = _earned_from_scores(c, drop) if c.get("scores") else None
        if earned is None:
            earned = _fraction(c.get("earned", 0), "earned", name) if weight - left > 1e-9 else 0.0
        cats.append({"name": name, "weight": weight, "left": left, "earned": earned})

    total = sum(c["weight"] for c in cats)
    if abs(total - 1) > 0.005:
        raise InputError(BAD_SUM, f"weights add up to {total * 100:.1f}%, not 100%")
    return cats


def compute(data):
    cats = load_categories(data)
    scale = data.get("scale") or DEFAULT_SCALE
    extra = float(data.get("extra_credit") or 0) / 100

    done = sum(c["weight"] - c["left"] for c in cats)
    left = sum(c["left"] for c in cats)
    points = sum(c["earned"] * (c["weight"] - c["left"]) for c in cats) + extra

    targets = []
    for letter, cutoff in sorted(scale.items(), key=lambda kv: -float(kv[1])):
        c = float(cutoff) / 100
        if left <= 1e-9:
            needed = 0.0 if points >= c else float("inf")
        else:
            needed = (c - points) / left
        targets.append({"letter": letter, "cutoff": float(cutoff), "needed": needed})

    return {
        "course": data.get("course") or "This class",
        "current": points / done if done > 1e-9 else None,
        "done": done,
        "left": left,
        "final": points if left <= 1e-9 else None,
        "targets": targets,
    }


def _pct(x):
    return f"{x * 100:.0f}%"


def _cutoff(t):
    return f"{t['letter']} ({t['cutoff']:g})"


def _deadline_line(deadline, today):
    try:
        d = date.fromisoformat(str(deadline))
    except ValueError:
        return None
    days = (d - today).days
    label = f"{d.strftime('%a %b')} {d.day}"
    if days < 0:
        return f"Withdrawal deadline passed ({label})"
    when = "today" if days == 0 else "1 day" if days == 1 else f"{days} days"
    return f"Withdraw by: {label} ({when})"


def render(result, deadline=None, today=None):
    lines = []
    name = result["course"]
    if result["final"] is not None:
        lines.append(f"{name}: {result['final'] * 100:.1f}% final (all graded)")
    elif result["current"] is None:
        lines.append(f"{name}: nothing graded yet ({_pct(result['left'])} of grade left)")
    else:
        lines.append(f"{name}: {result['current'] * 100:.1f}% now ({_pct(result['done'])} of grade done)")

    targets = result["targets"]  # highest cutoff first
    if result["final"] is None:
        reachable = [t for t in targets if t["needed"] <= 1 + 1e-9]
        if not reachable:
            lowest = targets[-1]
            lines.append(f"Need for {_cutoff(lowest)}: {_pct(lowest['needed'])} avg (out of reach)")
        else:
            best = reachable[0]
            above = targets[targets.index(best) - 1] if targets.index(best) > 0 else None
            if best["needed"] <= 0:
                lines.append(f"{_cutoff(best)} is locked in even with zeros on the rest")
            else:
                lines.append(f"Need for {_cutoff(best)}: {_pct(best['needed'])} avg on remaining")
            if above:
                lines.append(f"Need for {_cutoff(above)}: {_pct(above['needed'])} avg (out of reach)")
            secured = [t for t in reachable if t["needed"] <= 0 and t is not best]
            if secured and best["needed"] > 0:
                lines.append(f"Already locked in: {secured[0]['letter']}")

    if deadline:
        line = _deadline_line(deadline, today or date.today())
        if line:
            lines.append(line)
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="ClassPilot grade math")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--input", help="JSON file with grades and syllabus weights")
    src.add_argument("--sample", action="store_true", help="use assets/sample_grades.json")
    p.add_argument("--today", help="student's local date, YYYY-MM-DD")
    args = p.parse_args(argv)

    path = SAMPLE if args.sample else Path(args.input)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise InputError(BAD_INPUT, "input must be a JSON object")
        today = date.fromisoformat(args.today or data.get("today") or date.today().isoformat())
        print(render(compute(data), data.get("deadline"), today))
        return OK
    except InputError as e:
        print(f"error: {e}")
        return e.code
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"error: could not read {path.name}: {e}")
        return BAD_INPUT


if __name__ == "__main__":
    sys.exit(main())
