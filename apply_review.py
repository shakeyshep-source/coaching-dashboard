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


def parse_timestamp(ts):
    """Google Forms writes DD/MM/YYYY HH:MM:SS. Seconds are sometimes
    absent depending on the sheet's locale formatting."""
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts or "", fmt)
        except (ValueError, TypeError):
            continue
    return None


def handled_timestamps(latest, proposal):
    """Every response already dealt with, by submission timestamp - the
    only field the athlete cannot mistype."""
    seen = set()
    for src in (latest.get("athlete_response"), (proposal or {}).get("athlete_response")):
        if src and src.get("timestamp"):
            seen.add(src["timestamp"])
    for turn in latest.get("conversation") or []:
        if turn.get("athlete_timestamp"):
            seen.add(turn["athlete_timestamp"])
    return seen


def unhandled_responses(responses, handled, not_before):
    """Responses submitted on or after `not_before` that nothing has
    consumed yet, oldest first.

    Deliberately ignores the form's "Review date" field. That field is
    typed by hand and was never a reliable key: a question asked on the
    19th about the review of the 16th got dated the 19th, matched
    nothing, and was silently dropped - the form said "submitted", the
    dashboard showed nothing, and there was no error anywhere. The
    submission timestamp cannot be mistyped, so it is what we match on.
    """
    rows = []
    for r in responses:
        if r.get("timestamp") in handled:
            continue
        ts = parse_timestamp(r.get("timestamp"))
        if not_before and ts and ts.date() < not_before:
            continue
        rows.append((ts or datetime.min, r))
    rows.sort(key=lambda pair: pair[0])
    return [r for _, r in rows]


def as_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def record_review_query(responses, proposal=None):
    """Handle form responses that the approve/amend/reject gate cannot.

    A "hold" review writes no plan_proposal.json, so a response to it has
    no proposal to act on and used to be dropped on the floor - leaving
    no way to disagree with a decision to hold. Anything submitted since
    the current review that nothing else has consumed is treated as a
    question for the coach and attached to weekly_review_latest.json.
    """
    latest = load(LATEST_REVIEW_FILE)
    if not latest:
        return
    review_date = latest.get("review_date")
    pending = unhandled_responses(
        responses,
        handled_timestamps(latest, proposal),
        as_date(review_date),
    )
    if not pending:
        return

    # Each new question is a follow-up, not a duplicate: park the
    # exchange just finished in `conversation` and make the new one
    # current. An unanswered previous turn is kept too - two questions
    # inside the same minute must not cost the first one.
    for response in pending:
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

        log = load(DECISIONS_LOG_FILE, default=[])
        log.append({
            "logged_at": datetime.now().isoformat(timespec="seconds"),
            "proposal_id": None,
            "review_date": review_date,
            "response_timestamp": response.get("timestamp"),
            "decision": response.get("decision"),
            "action_taken": "review_query_logged",
            "athlete_thoughts": response.get("thoughts"),
            "changes_count": 0,
        })
        save(DECISIONS_LOG_FILE, log)

    save(LATEST_REVIEW_FILE, latest)
    print(f"Logged {len(pending)} question(s) against review {review_date} - "
          f"the coach reply session will answer. Plan untouched.")


def response_for_proposal(responses, proposal):
    """The athlete's decision on this proposal.

    Matches the typed "Review date" against the proposal id first. If
    that misses - the date is hand-entered and easily a day out - falls
    back to the newest unconsumed response submitted since the proposal
    was created. A mistyped date must not silently cost an approval.
    """
    latest = load(LATEST_REVIEW_FILE, default={}) or {}
    handled = handled_timestamps(latest, proposal)

    # Exclude anything already consumed. A question that *caused* this
    # proposal carries the same date as it, so an unfiltered date match
    # read the question itself as an "amend" on its own answer and
    # bounced the proposal straight to amend_requested (21 Aug).
    exact = [r for r in responses
             if r.get("review_date") == proposal.get("id")
             and r.get("timestamp") not in handled]
    if exact:
        return exact[-1], "review_date"

    created = parse_timestamp(proposal.get("created")) or None
    not_before = created.date() if created else as_date(proposal.get("id"))
    pending = unhandled_responses(responses, handled, not_before)
    if pending:
        return pending[-1], "timestamp"
    return None, None


def main():
    responses = load(RESPONSES_FILE, default=[])
    proposal = load(PROPOSAL_FILE)

    if not proposal:
        print("No plan proposal on file.")
        record_review_query(responses, proposal)
        return
    if proposal.get("status") not in ("pending", "amend_requested"):
        print(f"Proposal {proposal.get('id')} already {proposal.get('status')} - nothing to do.")
        record_review_query(responses, proposal)
        return

    response, matched_by = response_for_proposal(responses, proposal)
    if response and matched_by == "timestamp":
        print(f"Matched response {response.get('timestamp')} to proposal "
              f"{proposal.get('id')} by submission time - the form's review date "
              f"said {response.get('review_date')!r}.")
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
