# grades.py input

Write one JSON object to a file in the sandbox and pass it with `--input`.

```json
{
  "course": "CSE 230",
  "scale": {"A": 90, "B": 80, "C": 70, "D": 60},
  "categories": [
    {"name": "Homework", "weight": 0.25, "earned": 0.70, "left": 0.0},
    {"name": "Exams", "weight": 0.60, "earned": 0.42, "left": 0.45},
    {"name": "Labs", "weight": 0.15, "earned": 0.65, "left": 0.0}
  ],
  "drop_lowest": [],
  "extra_credit": 0,
  "deadline": "2026-11-04"
}
```

## Fields

- `scale`: the lowest percent for each letter, from the syllabus. Use plus/minus letters
  ("B+": 87) if the syllabus has them. Defaults to A 90, B 80, C 70, D 60.
- `categories`: one per syllabus category.
  - `weight`: the category's share of the final grade. 0.25 and 25 both work, but use the
    same style for every category; weights must add up to 100%.
  - `earned`: the student's average on the graded part of that category (0.70 or 70).
    Canvas shows this per assignment group.
  - `left`: the share of the final grade in this category that is not graded yet. In the
    example, exams are worth 60% and exam 3 plus the final (45% together) are left, so
    15% of exams is graded and averages 42%.
  - `scores` (optional): `[[earned, possible], ...]` for each graded item, used instead of
    `earned`. Needed for `drop_lowest`.
  - `drop_lowest` (optional): how many of the lowest `scores` to drop.
- `drop_lowest` (top level, optional): category names that drop their single lowest score.
- `extra_credit` (optional): percentage points added to the final grade (2 means +2%).
- `deadline` (optional): the withdrawal deadline, `YYYY-MM-DD`.

Pass the student's local date with `--today YYYY-MM-DD`; the sandbox clock may be in
another time zone.

## Canvas weighted groups

If Canvas uses weighted assignment groups, each group is a category: the group weight is
`weight`, the group's current score is `earned`, and the weight of its ungraded assignments
is `left`. If Canvas shows only a total with no weights, ask the student for the syllabus
weights.
