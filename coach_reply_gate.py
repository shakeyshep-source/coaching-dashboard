"""
coach_reply_gate.py — decides whether a coach session needs to run.

Prints GitHub Actions step outputs on stdout:

    reply=true|false
    modes=query,amend      (empty when reply=false)

TWO THINGS CAN BE WAITING, AND ONLY ONE USED TO FIRE
----------------------------------------------------
A form submission lands in one of two places, depending on whether the
last review proposed anything:

  - A review that proposed nothing has no gate to reach, so
    `apply_review.py` attaches the response to `weekly_review_latest.json`
    as `athlete_response` with `athlete_response_status: "logged"`.
    That is the ask-the-coach thread.

  - A review that DID propose gets the response attached to
    `plan_proposal.json` instead, and an "amend" decision sets its status
    to `amend_requested`.

Only the first fired the reply workflow. So on 20 Sep 2026, Shep replied
to a proposal at 18:13 saying he could not run the coming Sunday, and
nothing happened — by design the revision waited for the next weekly
review, six days later, to change a week that started the next morning.
He had to ask whether his comments had even arrived.

Both now fire. The loop terminates on its own: revising a proposal puts
it back to `pending`, and answering a query sets `answered`, so the next
run of this gate returns false either way.

This lives in one file because the same decision is made in three
workflows (review-response.yml, daily-pull.yml, coach-reply.yml) and it
was previously copy-pasted into each.
"""

import json

LATEST_FILE = "weekly_review_latest.json"
PROPOSAL_FILE = "plan_proposal.json"


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        # A missing or malformed file means nothing is waiting. Never let
        # it fail the gate — a broken JSON should not silence the coach
        # AND break the pipeline.
        return {}


def decide(latest, proposal):
    """Returns (query_waiting, amend_waiting) as booleans."""
    query = (bool((latest or {}).get("athlete_response"))
             and (latest or {}).get("athlete_response_status") == "logged")
    amend = (proposal or {}).get("status") == "amend_requested"
    return query, amend


def modes_for(query, amend):
    modes = []
    if query:
        modes.append("query")
    if amend:
        modes.append("amend")
    return modes


def main():
    query, amend = decide(load(LATEST_FILE), load(PROPOSAL_FILE))
    modes = modes_for(query, amend)
    print(f"reply={'true' if modes else 'false'}")
    print(f"modes={','.join(modes)}")


if __name__ == "__main__":
    main()
