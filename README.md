# ClassPilot: stay, drop or swap

A [Cubbie](https://cubbie.ai) Skill that tells a student, in iMessage, whether to stay in a
class, drop it or swap it, and watches next term's ASU seats so the backup plan is real.

- **Grade check.** Exact math from Canvas grades and syllabus weights: what you need on the
  rest of the class for each letter grade.
- **Deadline guard.** The real course withdrawal deadline from ASU class search, plus a reminder.
- **Seat watch.** Watches full sections and texts the student when a seat opens.
- **Swap finder.** Lists sections that fit the student's schedule and opens ASU's add or
  swap page for the student to confirm.
- **Drafts.** Drafts an email to the professor or advisor; sent only after the student says yes.

## Install on Cubbie

Text Cubbie the Skill folder link:

    https://github.com/kp-krish/ClassPilot/tree/main/skill/classpilot

Then try "should I drop CSE 230?" or "tell me if a seat opens in CSE 230 for spring".

## How it works

```
Student (iMessage)
  └─ Cubbie + SKILL.md
       ├─ scripts/grades.py   grade math, runs locally, sends nothing
       └─ scripts/seats.py    ASU class search (direct) + Seat API (watcher ID + class numbers only)
                                   │ HTTPS
AWS (us-east-2)                    ▼
  API Gateway HTTP API + Lambda (api.py) ── DynamoDB: watches, sections, alerts
  EventBridge Scheduler, every minute ──▶ Lambda (poller.py) ──▶ ASU class search
```

The poller fetches each watched course once a minute, however many students watch it, and
writes one alert per watcher when a section's unreserved open seats go from 0 to more than 0.
Cubbie's scheduled check-in runs `seats.py alerts`, which returns and clears pending alerts.
Alerts expire after 24 hours and idle watches after 30 days.

## Repository

```
skill/classpilot/        the Cubbie Skill (what students install)
  SKILL.md
  references/grade-input.md
  scripts/grades.py      grade math
  scripts/seats.py       ASU + Seat API client
  assets/                sample data for --sample mode
backend/                 AWS SAM app
  template.yaml
  src/api.py             Seat API routes
  src/poller.py          1-minute seat check
  src/asu_seats.py       ASU fetch and parse
  src/store.py           DynamoDB access
  src/terms.py           "Spring 2027" <-> 2271
  tests/
tests/test_grades.py
```

## Seat API

| Method and path | Purpose |
|---|---|
| `POST /watchers` | Create a random watcher ID |
| `GET /sections?term=&course=` | Sections with seats, days, times, instructor |
| `POST /watches` `{watcher_id, term, class_nbr}` | Watch a section (max 10 per watcher) |
| `DELETE /watches/{class_nbr}?watcher_id=` | Stop watching |
| `GET /watches?watcher_id=` | List watches with the latest seat counts |
| `GET /alerts?watcher_id=` | Return and clear pending seat openings |

Errors are `{"error": "..."}` with 400, 404, 429 or 502. Throttled at 5 requests per second
per route (burst 10).

## Develop

```bash
python -m venv .venv && .venv/Scripts/pip install boto3 "moto[dynamodb]" pytest
.venv/Scripts/python -m pytest tests backend/tests

python skill/classpilot/scripts/grades.py --sample
python skill/classpilot/scripts/seats.py sections --term "Spring 2027" --course "CSE 230" --sample
```

Deploy (region and stack name are in `backend/samconfig.toml`):

```bash
cd backend && sam build && sam deploy
# optional alarm and budget emails:
sam deploy --parameter-overrides AlertEmail=you@example.com
```

## Privacy

Grades stay in the student's Cubbie sandbox. The backend stores only random watcher IDs,
terms and class numbers: no names, emails or grades. ClassPilot never enrolls, drops or
sends email on its own.

ASU's class search data endpoint is public but not documented for outside developers and
may change. Seat watching supports ASU only; grade math works anywhere.
