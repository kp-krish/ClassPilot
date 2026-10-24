import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skill" / "classpilot" / "scripts"))
import grades  # noqa: E402

SAMPLE = json.loads((grades.SAMPLE).read_text())


def run(data, tmp_path, capsys, today="2026-10-24"):
    f = tmp_path / "in.json"
    f.write_text(json.dumps(data))
    code = grades.main(["--input", str(f), "--today", today])
    return code, capsys.readouterr().out.strip()


def test_sample_matches_design_doc(capsys):
    assert grades.main(["--sample"]) == 0
    assert capsys.readouterr().out.strip().splitlines() == [
        "CSE 230: 61.0% now (55% of grade done)",
        "Need for C (70): 81% avg on remaining",
        "Need for B (80): 103% avg (out of reach)",
        "Withdraw by: Wed Nov 4 (11 days)",
    ]


def test_needed_formula():
    r = grades.compute(SAMPLE)
    need = {t["letter"]: t["needed"] for t in r["targets"]}
    assert r["current"] == pytest.approx(0.61)
    assert need["C"] == pytest.approx((0.70 - 0.3355) / 0.45)
    assert need["D"] < need["C"] < 1 < need["B"]


def test_percent_inputs_equal_fractions():
    pct = {**SAMPLE, "categories": [
        {"name": "Homework", "weight": 25, "earned": 70, "left": 0},
        {"name": "Exams", "weight": 60, "earned": 42, "left": 45},
        {"name": "Labs", "weight": 15, "earned": 65, "left": 0}]}
    assert grades.compute(pct)["current"] == pytest.approx(0.61)


def test_secured_grade(tmp_path, capsys):
    data = {**SAMPLE, "categories": [
        {"name": "Work", "weight": 0.9, "earned": 0.95, "left": 0},
        {"name": "Final", "weight": 0.1, "left": 0.1}]}
    code, out = run(data, tmp_path, capsys)
    assert "Need for A (90): 45% avg on remaining" in out and "Already locked in: B" in out
    data["categories"][0]["earned"] = 1.0
    code, out = run(data, tmp_path, capsys)
    assert "A (90) is locked in even with zeros on the rest" in out


def test_nothing_reachable(tmp_path, capsys):
    data = {**SAMPLE, "categories": [
        {"name": "Work", "weight": 0.8, "earned": 0.3, "left": 0},
        {"name": "Final", "weight": 0.2, "left": 0.2}]}
    code, out = run(data, tmp_path, capsys)
    assert "Need for D (60): 180% avg (out of reach)" in out


def test_all_graded(tmp_path, capsys):
    data = {**SAMPLE, "categories": [{"name": "All", "weight": 1, "earned": 0.84, "left": 0}]}
    code, out = run(data, tmp_path, capsys)
    assert out.splitlines()[0] == "CSE 230: 84.0% final (all graded)"


def test_drop_lowest_and_extra_credit():
    data = {"course": "X", "extra_credit": 2, "categories": [
        {"name": "Quizzes", "weight": 0.5, "left": 0, "drop_lowest": 1,
         "scores": [[2, 10], [8, 10], [9, 10]]},
        {"name": "Final", "weight": 0.5, "left": 0.5}]}
    r = grades.compute(data)
    assert r["current"] == pytest.approx((0.85 * 0.5 + 0.02) / 0.5)


def test_deadline_passed(tmp_path, capsys):
    code, out = run(SAMPLE, tmp_path, capsys, today="2026-11-05")
    assert out.endswith("Withdrawal deadline passed (Wed Nov 4)")


@pytest.mark.parametrize("data,code", [
    ({"course": "X"}, 2),
    ({"categories": [{"name": "A", "earned": 0.9}]}, 2),
    ({"categories": [{"name": "A", "weight": 0.5, "earned": 0.9}]}, 3),
    ({"categories": [{"name": "A", "weight": 1, "earned": 0.9, "left": 1.2}]}, 4),
    ([1, 2], 4),
])
def test_exit_codes(tmp_path, capsys, data, code):
    assert run(data, tmp_path, capsys)[0] == code


def test_unreadable_file(tmp_path, capsys):
    f = tmp_path / "bad.json"
    f.write_text("{not json")
    assert grades.main(["--input", str(f)]) == 4
