# Automation setup — one-time steps

The pipeline now runs in GitHub Actions twice each morning, so the
laptop never needs to be open again. Three things need doing once to
switch it on, plus one clean-up.

## 1. Add the Garmin token secret (5 min, on the laptop)

```bash
cd ~/coaching-dashboard   # wherever the repo lives on the laptop
bash export_garmin_tokens.sh
```

Copy the long output line, then on GitHub:
**Settings → Secrets and variables → Actions → New repository secret**
- Name: `GARMIN_TOKENS_B64`
- Value: the pasted line

The token lasts about a year. If the daily Action starts failing with
Garmin auth errors, run the script again and update the secret.

To test immediately: **Actions tab → Daily data pull → Run workflow.**

## 2. Create the weekly review response form (10 min)

Create one more Google Form, exactly like the existing three, called
e.g. **"Weekly review response"**, with these questions (titles must
match exactly — the pipeline parses the sheet by column name):

| Question title | Type |
|---|---|
| `Review date` | Date — the date shown on the review you're answering |
| `Decision` | Multiple choice: `Approve` / `Amend` / `Reject` |
| `Thoughts` | Paragraph — your reasoning, amendments, anything the coach should factor in |

Then:
1. Link it to a response spreadsheet (Responses → link to Sheets).
2. Share the spreadsheet as **"Anyone with the link can view"** (same
   as the other three).
3. Send Claude (or edit yourself) two things:
   - the spreadsheet ID → goes in `REVIEW_RESPONSE_SHEET_ID` in
     `sheets_pull.py`
   - the form's fill-in URL → goes in `REVIEW_FORM_URL` near the top of
     the script in `index.html` (so the Coach tab's "Respond" button
     works from your phone)

How decisions behave:
- **Approve** → next pipeline run merges the proposed sessions into the
  live plan.
- **Amend** → your thoughts are attached to the proposal; the next
  weekly coach session revises it (or ask Claude any time to revise it
  sooner).
- **Reject** → proposal archived, plan untouched.

## 3. Add the Claude token for the weekly coach session (5 min)

The Sunday-evening review runs as a Claude Code session inside GitHub
Actions. It authenticates with your existing Claude subscription — no
API billing. On the laptop (or anywhere the Claude Code CLI is
installed and logged in):

```bash
claude setup-token
```

Copy the token it prints into a new repository secret named
`CLAUDE_CODE_OAUTH_TOKEN` (same place as step 1).

(Alternative: create an Anthropic API key instead and store it as
`ANTHROPIC_API_KEY` — the workflow accepts either.)

To test: **Actions tab → Weekly coach review → Run workflow** — a
review + (if warranted) a proposal should appear on the Coach tab a few
minutes later.

## 4. Merge this branch to `main`

Scheduled GitHub Actions only run from the default branch, and the
weekly coach review reads/writes `main`. Merge
`claude/review-previous-dashboard-802lfc` → `main` to go live.

## Google Forms — publish them, and keep the links here

A Google Form must be **published** before anyone, including its owner,
can submit. An unpublished form answers its responder link with
"We're sorry. This document is not published." — which is exactly how
two weeks of daily logs went missing: the entries were never rejected,
they were never submittable. If a form ever stops accepting entries,
check Publish first.

Responder links currently wired into `index.html`:

| Form | Where it appears | Link |
|---|---|---|
| Daily running log | Hero "Log today's session" + header | `/forms/d/e/1FAIpQLSe7Bnpz45kG…/viewform` |
| Weekly review response | Coach tab "Respond" | `/forms/d/e/1FAIpQLSelVvuccMxI…/viewform` |
| Training plan entry | Header | `/forms/d/e/1FAIpQLSfnpMureLGU…/viewform` |
| Race Result Log | Header | `/forms/d/e/1FAIpQLSeyiW4JbXtw…/viewform` |

Note these are the `/forms/d/e/<responder-id>/viewform` links from each
form's Send dialog — **not** the `/forms/d/<file-id>/` editing URL, which
only works for the owner and not as a responder link.

## 6. Make form submissions trigger the pipeline instantly (10 min)

Without this, a form entry waits for the next scheduled run — and
scheduled runs on this repo have arrived up to 2.5 hours late. With it,
submitting a form fires the pipeline within about a minute, so a query
on the weekly review is answered while you are still thinking about it.

**a. Create a token GitHub will accept.**
On GitHub: **Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token**
- Repository access: **Only select repositories** → `coaching-dashboard`
- Repository permissions: **Contents → Read and write**
  (that is what the dispatch endpoint requires; nothing else is needed)
- Expiry: whatever you are comfortable re-doing — 1 year is fine

Copy the token now; GitHub won't show it again.

**b. Add the script to the responses spreadsheet.**
Open the spreadsheet the forms write to →
**Extensions → Apps Script**. Delete the placeholder code, paste the
contents of `form_submit_trigger.gs` from this repo, and save.

**c. Give the script the token.**
In the Apps Script editor: **Project Settings (gear) → Script
Properties → Add script property**
- Property: `GH_TOKEN`
- Value: the token from step (a)

The token lives in the script's own properties, not in the code — so it
is never in a file, and never in this repo.

**d. Wire it to form submissions.**
In the Apps Script editor: **Triggers (clock icon) → Add trigger**
- Function: `onFormSubmitNudgeGitHub`
- Event source: **From spreadsheet**
- Event type: **On form submit**

Google will ask you to authorise the script — it is your own script on
your own sheet, so approve it.

**e. Test.** Submit anything on any of the forms, then watch the
**Actions** tab: a **Form response** run should appear within a minute.

One trigger covers all four forms, since they all write to the same
spreadsheet — so logging a session updates the dashboard immediately
too, not just review responses.

If the trigger ever breaks, nothing is lost: the same workflow also
runs hourly through the day as a backstop, and the morning pull catches
anything the hourly missed.

## 5. Retire the laptop cron

Once the first cloud run has committed successfully (check the Actions
tab), remove the crontab entry on the laptop:

```bash
crontab -e   # delete the run_all.py line
```

Leaving it on won't corrupt anything (both sides pull/rebase), but two
writers is pointless noise.

## What runs when (all times UK)

| When | What |
|---|---|
| Within ~1 min of any form submission | Forms sync, decision applied, and any query on the review answered |
| Hourly, 06:00–22:00 | Backstop for the above, in case the Apps Script trigger fails |
| ~06:15 daily | Full data pull (Garmin + weather) → dashboard fresh before 8am |
| ~11:30 daily | Catch-up pull (late watch sync, morning form entries) |
| Sunday ~18:40 | Scheduled Claude coach session (GitHub Action): weekly review + plan proposal |

Approving a proposal applies it on the next run — which, with step 6 in
place, is roughly a minute after you submit the form, not the next
morning.
