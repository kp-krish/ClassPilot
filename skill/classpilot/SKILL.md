---
name: classpilot
description: Tells a student whether to stay in, drop or swap a class using their Canvas grades, withdrawal deadlines and live ASU seat openings. Use when a student asks if they should drop a class, what they need on the final, whether they are passing, when the withdrawal deadline is, or wants an alert when a seat opens in a full ASU class or another section that fits their schedule.
---

# ClassPilot: stay, drop or swap

A student at a 62 rarely knows what they need to pass, when the W deadline is, or whether
they could get the class next term. ClassPilot answers all three in one thread: exact grade
math from Canvas, the real withdrawal deadline, and a seat watcher for next term.

## When to use

- "should I drop CSE 230", "what do I need on the final", "am I passing chem"
- "when's the withdrawal deadline", "remind me before the drop deadline"
- "tell me if a seat opens in CSE 230", "is there another section that fits my schedule"

Not for study plans or general emails to professors; Cubbie's own abilities cover those.
Grade math works for any school. Section lookups, deadlines and seat watching are ASU only.

## What leaves the student's phone

Grades never leave the student's sandbox: `scripts/grades.py` runs locally and sends nothing.
To watch a class, `scripts/seats.py` sends the ClassPilot seat service a random watcher ID,
the term and class numbers. Nothing else: no name, email, ASU ID or grades. Section lookups
and deadlines come from ASU's public class search; when ASU blocks the sandbox, they go
through the seat service, which then sees only the course and term. Tell the student this in one
line the first time they turn on a seat watch.

## Setup (first use only)

Run `python3 scripts/seats.py init`. It creates the student's random watcher ID and keeps
it under the student's home folder. Nothing to install; scripts use only the Python
standard library.

## Flow A: stay or drop (main flow)

1. Read the student's Canvas grade and graded assignments for the course. If Canvas is not
   connected, ask them to connect it, or ask for their current grade and what is left
   (for example "final is 30%, exam 3 is 15%").
2. Read the syllabus in the Canvas course files for the category weights, the letter grade
   cutoffs and any drop-lowest or extra credit rules. If weights are missing, ask the
   student. Never guess weights.
3. Write the inputs to a JSON file in the sandbox (format in `references/grade-input.md`)
   and run, with the student's local date:
   `python3 scripts/grades.py --input <file> --today <YYYY-MM-DD>`
4. For an ASU class, get the real withdrawal deadline:
   `python3 scripts/seats.py deadline --term "<current term>" --course "<course>"`
   Use the "course withdrawal by" date for the student's session (add `--class <nbr>` if
   you know their class number) as `deadline` in the JSON, and run step 3 again if you
   had not included it. For other schools, ask or read the registrar's calendar.
5. Text the result as is: current grade, what is needed for the best reachable grade,
   and the withdrawal deadline. Do not add a recommendation the math does not support.
6. Offer the backup, one question at a time:
   - "Want me to check next term's sections?" (Flow B)
   - "Want a reminder 2 days before the deadline?" On yes, create a one-time reminder,
     worded to stand alone: "ClassPilot: <course> withdrawal deadline is <date>. Run the
     grade check again and text the student the result."

Always add one line when dropping is on the table: "Check with your advisor first if
financial aid, a scholarship or a visa depends on your credits."

## Flow B: watch and swap (ASU)

1. Get the course and term ("Spring 2027"). If the student names a section, confirm its
   5-digit class number.
2. Run `python3 scripts/seats.py sections --term "<term>" --course "<course>"`. It tries
   ASU class search directly, then the ClassPilot seat service, so trust its output and
   don't look the class up in the browser instead; ASU's catalog page needs JavaScript.
3. Compare section days and times with the student's schedule from Canvas or Google
   Calendar, and with the campus the student attends (each line shows Tempe, Poly, West,
   Downtown or ASU Online; ASU Online sections are for ASU Online students only). Show only
   the sections that fit (up to 3), with their class numbers, then ask which to watch.
   If you don't know their schedule yet, show the best few and ask which days and campus work.
   Mention reserved seats when the output does: those seats are held for certain majors.
4. Ask which to watch. On yes, run for each:
   `python3 scripts/seats.py watch --term "<term>" --class <class number>`
   If it says seats are open right now, tell the student at once; there is nothing to wait for.
5. Offer a recurring check-in: "Want me to check for openings every 15 minutes?" Use the
   shortest interval Cubbie allows. Create it with exactly this wording, so it works days
   later on its own:
   "ClassPilot seat check: run `python3 scripts/seats.py alerts` in the classpilot Skill
   folder. If the output starts with ACTION, text the student the Seat open message it
   gives, word for word, right away; it is time-sensitive and the student asked for it.
   If it says No new seat openings, send nothing."
6. On an opening, text it, say plainly that seats go fast, and ask "Open enrollment?".
   On yes, open the Add class link from the alert (or the Swap class link if they are
   swapping sections) in the browser and hand it to the student to sign in and confirm.
   Never enroll, drop or swap for them.
7. "Any seat openings?" or "did a seat open?" runs `python3 scripts/seats.py alerts --recent`,
   which shows every opening from the last 24 hours, including ones a check-in already
   fetched. "What am I watching?" runs `python3 scripts/seats.py watches`. "Stop watching
   <class>" runs `python3 scripts/seats.py unwatch --class <class number>`. When nothing
   is left to watch, offer to remove the recurring check-in.

## Flow C: drafts

When the student wants to ask the professor about options (an incomplete, a regrade, what
the final covers) or their advisor about dropping, draft a short email in Gmail with the
facts from Flow A. Show the draft. Send only after the student says yes.

## Replies

- Short, plain text, one idea per message; no Markdown, tables or code.
- Lead with the numbers: "You're at 61% in CSE 230." Then what's needed, then the deadline.
- Links on their own line.
- Advice, not decisions: show the math and the deadline; the student decides.
- Ask one question at a time, with a default they can accept with "yes".

Example:

    You're at 61% in CSE 230. Exam 3 and the final are left (45% of the grade).
    You need an 81 average on both for a C. A B is out of reach.
    Withdrawal deadline: Wed Nov 4.

    Spring has an open section, Th 12:00, that fits your schedule. Want me to watch it?

## When something fails

Each script prints one `error:` line and exits with a code. Reply with one line:

| Script and exit code | Reply |
|---|---|
| grades.py 2 | "I couldn't find the grade weights. What % is each part of the class worth?" |
| grades.py 3 | "Those weights don't add up to 100%. Can you check the syllabus numbers?" |
| grades.py 4 | Fix the JSON you wrote and run it again; if it still fails, ask for the current grade and what's left. |
| seats.py 5 | "The seat watcher is down right now. I can still look up sections; I'll try the watch again later." |
| seats.py 6 | "I can't find that class for <term>. What's the 5-digit class number?" |
| seats.py 7 | "You're watching the max of 10 classes. Want to stop watching one?" |
| seats.py 8 | Check the term ("Spring 2027") and the 5-digit class number, then run it again. |
| seats.py 9 | "ASU class search isn't responding right now. Try again in a few minutes, or check https://catalog.apps.asu.edu/catalog/classes" |
| Canvas not connected | "Connect Canvas and I can pull your grades, or tell me your grade and what's left." |

## Trying it without an account

Every command takes `--sample`, which uses `assets/sample_grades.json` or
`assets/sample_sections.json` and leaves the student's saved state alone:
`python3 scripts/grades.py --sample` and `python3 scripts/seats.py sections --term "Spring 2027" --course "CSE 230" --sample`.
