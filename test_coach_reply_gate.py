"""
test_coach_reply_gate.py

Run with: python3 test_coach_reply_gate.py
"""

import sys

import coach_reply_gate as g

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}\n    got:  {got!r}\n    want: {want!r}")


LOGGED = {"athlete_response": {"thoughts": "?"}, "athlete_response_status": "logged"}
ANSWERED = {"athlete_response": {"thoughts": "?"}, "athlete_response_status": "answered"}
AMEND = {"id": "2026-09-20", "status": "amend_requested"}
PENDING = {"id": "2026-09-20", "status": "pending"}
APPLIED = {"id": "2026-09-20", "status": "applied"}

check("nothing waiting", g.decide({}, {}), (False, False))
check("a logged query fires", g.decide(LOGGED, APPLIED), (True, False))

# The case this file exists for: 20 Sep 2026, an amend on a live
# proposal, which used to fire nothing at all.
check("an amend_requested proposal fires", g.decide({}, AMEND), (False, True))
check("both can be waiting at once", g.decide(LOGGED, AMEND), (True, True))

# Termination: the session's own output turns the gate off.
check("answered stops the query firing", g.decide(ANSWERED, APPLIED), (False, False))
check("pending stops the amend firing", g.decide({}, PENDING), (False, False))
check("applied stops the amend firing", g.decide({}, APPLIED), (False, False))

# A response attached to a proposal must not also look like a query.
check("amend response is not a query", g.decide({}, {**AMEND, "athlete_response": {"x": 1}}),
      (False, True))

# Missing / malformed files must not fire, and must not raise.
check("no files at all", g.decide(None, None), (False, False))
check("latest present but empty", g.decide({}, None), (False, False))
check("athlete_response with no status", g.decide({"athlete_response": {"t": 1}}, {}),
      (False, False))
check("status logged but no response", g.decide({"athlete_response_status": "logged"}, {}),
      (False, False))

check("modes empty", g.modes_for(False, False), [])
check("modes query", g.modes_for(True, False), ["query"])
check("modes amend", g.modes_for(False, True), ["amend"])
check("modes both, query first", g.modes_for(True, True), ["query", "amend"])

# load() must swallow anything rather than break the pipeline.
check("load of a missing file", g.load("no_such_file_here.json"), {})

if failures:
    print(f"{len(failures)} FAILED:\n")
    print("\n\n".join(failures))
    sys.exit(1)
print(f"all {14} gate tests passed")
