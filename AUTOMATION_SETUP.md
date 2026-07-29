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
| ~06:15 daily | Full data pull → dashboard fresh before 8am |
| ~11:30 daily | Catch-up pull (late watch sync, morning form entries) |
| Sunday ~18:40 | Scheduled Claude coach session (GitHub Action): weekly review + plan proposal |
| Next morning after you respond | `apply_review.py` applies / flags your decision |
