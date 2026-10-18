---
name: weekly-review
description: Sunday weekly coach review for Shep — writes reviews/YYYY-MM-DD.md, weekly_review_latest.json and a three-week plan_proposal.json. Use when running the scheduled Sunday review or when explicitly asked to do the weekly review; not for mid-week questions or ad-hoc session analysis.
---

# Weekly review procedure (scheduled coach session)

Runs every Sunday early afternoon (~13:40 UK, backstop ~16:40), after
its own Garmin pull — so the Sunday long run is always in the week being
reviewed. CLAUDE.md is the coaching brief and governs everything here;
this file is only the procedure. The proposal format, the three-weeks-
deep rule and amend handling live in CLAUDE.md under "Plan proposals",
because the mid-week reply session uses them too.

The session must:

1. `git pull` latest `main`; read the brief (CLAUDE.md),
   `weekly_summary.json`, `computed_data.json`, `manual_log.json`
   (notes!), `training_plan.json`, `plan_proposal.json`,
   `review_responses.json`, `race_prediction.json`,
   `weekly_review_latest.json` (last week's review and any response to
   it).
2. If the current proposal is `amend_requested`: handle it per "Plan
   proposals → Amend requests" in CLAUDE.md, and skip to step 5.
2b. **Answer any query on the last review.** A "hold" review proposes
   nothing, so a response to it can't reach the proposal gate — instead
   `apply_review.py` attaches it to `weekly_review_latest.json` as
   `athlete_response` with `athlete_response_status: "logged"`. If last
   week's review carries one and this session hasn't answered it yet,
   open the new review by addressing it directly: what he said, whether
   it changes the read, and if it does, propose the change (step 5).
   Disagreeing with a hold is a legitimate input, not noise — but it
   doesn't override the evidence on its own. Say plainly which it is.
   If `athlete_response_status` is already `"answered"`, the mid-week
   reply (see "Answering him" in CLAUDE.md) dealt with it — read that
   reply, carry its conclusion forward and say how the week since bore
   it out. Do not re-argue it from scratch.
3. Write the weekly review to `reviews/YYYY-MM-DD.md` (that day's date,
   Europe/London): how the week actually went vs plan, how he's coping
   (data + his own words), recovery trends, achilles, efficiency trend,
   race countdown, and a clear recommendation: **hold the plan** or
   **change it** (and exactly what/why).
   **Concise by default** — the brief's rule, and it applies here. Aim
   for about 700 words; go longer only when the week genuinely demands
   it (an injury flag, a changed plan) or he has asked for more. Cut
   repetition between sections before cutting evidence.
4. Write `weekly_review_latest.json`:
   ```json
   {
     "review_date": "YYYY-MM-DD",
     "week_start": "YYYY-MM-DD",
     "headline": "one-sentence verdict",
     "recommendation": "hold" | "adjust",
     "review_md": "<full review markdown>",
     "proposal_id": "YYYY-MM-DD" | null,
     "proposal_status": "pending" | null
   }
   ```
   Write it fresh each week — do **not** carry last week's
   `athlete_response`, `athlete_response_status`, `coach_reply` or
   `conversation` across, or the dashboard will show old exchanges
   against a new review. Read the whole of last week's `conversation`
   first, though: what he asked mid-week is part of how the week went.
5. **Write `plan_proposal.json` covering the next three Monday–Sunday
   weeks**, per "Plan proposals" in CLAUDE.md (`id` = review_date).
   Most Sundays write a proposal even when the recommendation is
   **hold** — "hold" is about whether anything already planned should
   change; extending the horizon by a week is not a change of course.
   Say which it is in the rationale's first line.
   **Never edit `training_plan.json` yourself.**
6. Commit everything and push to `main` (commit message:
   `Weekly coach review YYYY-MM-DD`) — unless running inside the
   weekly-review GitHub Action, whose final step commits for you.

Shep responds via the weekly review Google Form (approve / amend /
reject + thoughts); the next morning's pipeline applies his decision.
