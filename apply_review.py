"""
apply_review.py — PLAN CHANGE GATE

Job: the only code allowed to modify training_plan.json automatically,
and only ever with the athlete's explicit sign-off.

The loop it closes:
  1. Weekly coach review (scheduled Claude session) writes
     plan_proposal.json with status "pending" — it NEVER touches
     training_plan.json directly.
  2. Athlete responds via the weekly review Google Form
     (approve / amend / reject + thoughts) -> review_responses.json.
  3. This script, run daily in the pipeline, matches the response to the
     pending proposal:
       - approve -> merge proposed sessions into training_plan.json,
                    mark proposal "applied"
       - amend   -> mark proposal "amend_requested" and attach the
                    athlete's thoughts; the next coach session revises it
       - reject  -> mark proposal "rejected", plan untouched
  4. Every decision is archived to reviews/decisions_log.json so there's
     a permanent record of what changed, when, and why.

No response yet -> proposal stays "pending" and nothing happens. The
plan never changes without the athlete in the loop. Ever.
"""

import json
import os
from datetime import datetime

PROPOSAL_FILE = "plan_proposal.json"
RESPONSES_FILE = "review_responses.json"
PLAN_FILE = "training_plan.json"
LATEST_REVIEW_FILE = "weekly_review_latest.json"
DECISIONS_LOG_FILE = "reviews/decisions_log.json"


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def merge_plan(plan_rows, changes, applied_at):
    """Merge proposed sessions into the plan by date — proposal wins.
    A change with session_type null/"remove" deletes that date's entry.

    Each merged session is stamped with when it was applied, so a plan
    entry submitted earlier via the training-plan form can't re-apply on
    the next pull and revert a change the athlete just approved.
    """
    by_date = {r["date"]: r for r in plan_rows}
    for change in changes:
        if change.get("session_type") in (None, "remove"):
            by_date.pop(change["date"], None)
        else:
            by_date[change["date"]] = {
                "date": change["date"],
                "session_type": change.get("session_type"),
                "target_distance_km": change.get("target_distance_km"),
                "target_pace": change.get("target_pace"),
                "notes": change.get("notes"),
                "source": "coach",
                "updated_at": applied_at,
            }
    return sorted(by_date.values(), key=lambda r: r["date"])


def log_decision(proposal, response, action):
    log = load(DECISIONS_LOG_FILE, default=[])
    log.append({
        "logged_at": datetime.now().isoformat(timespec="seconds"),
        "proposal_id": proposal.get("id"),
        "decision": response.get("decision"),
        "action_taken": action,
        "athlete_thoughts": response.get("thoughts"),
        "changes_count": len(proposal.get("changes", [])),
    })
    save(DECISIONS_LOG_FILE, log)


def update_latest_review_status(proposal_id, status):
    latest = load(LATEST_REVIEW_FILE)
    if latest and latest.get("proposal_id") == proposal_id:
        latest["proposal_status"] = status
        save(LATEST_REVIEW_FILE, latest)


def record_review_query(responses):
    """Handle a form response against a review that proposed nothing.

    A "hold" review writes no plan_proposal.json, so the approve /amend /
    reject gate above has nothing to match and the response used to be
    dropped on the floor - leaving no way to disagree with a decision to
    hold. Attach it to weekly_review_latest.json and log it, so the next
    coach session reads it and the dashboard can show it was received.
    """
    latest = load(LATEST_REVIEW_FILE)
    if not latest or latest.get("proposal_id"):
        return
    review_date = latest.get("review_date")
    matches = [r for r in responses if r.get("review_date") == review_date]
    if not matches:
        return
    response = matches[-1]

    if (latest.get("athlete_response") or {}).get("timestamp") == response.get("timestamp"):
        print(f"Response on review {review_date} already recorded - waiting for the next coach session.")
        return

    # A second question in the same week is a follow-up, not a duplicate:
    # park the exchange just finished in `conversation` and make the new
    # one current. Anything unanswered is kept too - a question asked
    # twice in the minute before a reply lands must not be dropped.
    previous = latest.get("athlete_response")
    if previous:
        history = latest.get("conversation") or []
        history.append({
            "athlete_timestamp": previous.get("timestamp"),
            "athlete_decision": previous.get("decision"),
            "athlete_thoughts": previous.get("thoughts"),
            "coach_reply": latest.get("coach_reply"),
            "coach_reply_at": latest.get("coach_reply_at"),
        })
        latest["conversation"] = history

    latest["athlete_response"] = response
    latest["athlete_response_status"] = "logged"
    latest.pop("coach_reply", None)
    latest.pop("coach_reply_at", None)
    save(LATEST_REVIEW_FILE, latest)

    log = load(DECISIONS_LOG_FILE, default=[])
    log.append({
        "logged_at": datetime.now().isoformat(timespec="seconds"),
        "proposal_id": None,
        "review_date": review_date,
        "decision": response.get("decision"),
        "action_taken": "review_query_logged",
        "athlete_thoughts": response.get("thoughts"),
        "changes_count": 0,
    })
    save(DECISIONS_LOG_FILE, log)
    print(f"Response logged against review {review_date} (no proposal to act on) - "
          f"the next coach session will answer it. Plan untouched.")


def main():
    responses = load(RESPONSES_FILE, default=[])
    proposal = load(PROPOSAL_FILE)

    if not proposal:
        print("No plan proposal on file.")
        record_review_query(responses)
        return
    if proposal.get("status") not in ("pending", "amend_requested"):
        print(f"Proposal {proposal.get('id')} already {proposal.get('status')} - nothing to do.")
        record_review_query(responses)
        return

    response = next(
        (r for r in responses if r.get("review_date") == proposal.get("id")), None
    )
    if not response:
        print(f"Proposal {proposal.get('id')} is {proposal.get('status')} - no athlete response yet.")
        return

    decision = (response.get("decision") or "").lower()

    if decision == "approve":
        applied_at = datetime.now().isoformat(timespec="seconds")
        plan = load(PLAN_FILE, default=[])
        merged = merge_plan(plan, proposal.get("changes", []), applied_at)
        save(PLAN_FILE, merged)
        proposal["status"] = "applied"
        proposal["athlete_response"] = response
        proposal["applied_at"] = applied_at
        save(PROPOSAL_FILE, proposal)
        update_latest_review_status(proposal["id"], "applied")
        log_decision(proposal, response, "plan_updated")
        print(f"APPROVED: merged {len(proposal.get('changes', []))} session(s) into {PLAN_FILE}.")

    elif decision == "amend":
        if proposal.get("status") == "amend_requested" and \
           proposal.get("athlete_response", {}).get("timestamp") == response.get("timestamp"):
            print("Amendment already recorded - waiting for the next coach session to revise.")
            return
        proposal["status"] = "amend_requested"
        proposal["athlete_response"] = response
        save(PROPOSAL_FILE, proposal)
        update_latest_review_status(proposal["id"], "amend_requested")
        log_decision(proposal, response, "amendment_requested")
        print("AMEND requested - thoughts attached for the next coach session. Plan untouched.")

    elif decision == "reject":
        proposal["status"] = "rejected"
        proposal["athlete_response"] = response
        save(PROPOSAL_FILE, proposal)
        update_latest_review_status(proposal["id"], "rejected")
        log_decision(proposal, response, "proposal_rejected")
        print("REJECTED - proposal archived, plan untouched.")

    else:
        print(f"Unrecognised decision '{response.get('decision')}' - plan untouched.")


if __name__ == "__main__":
    main()
