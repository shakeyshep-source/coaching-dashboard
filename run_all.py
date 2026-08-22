"""
run_all.py — ORCHESTRATION

Job: run every pull script, then build_computed.py, in the right
order. One entry point for cron instead of four separate commands.

If any single step fails, it's logged and the script moves on to the
next one rather than aborting — a failed weather pull shouldn't stop
Garmin data or the computed layer from updating. The final "computed"
step still runs even if an earlier pull failed, using whatever data
files already exist on disk (stale weather is better than no dashboard
update at all).
"""

import subprocess
import sys
from datetime import datetime

STEPS = [
    ("garmin_pull.py", "Garmin data pull"),
    ("weather_pull.py", "Weather pull"),
    ("sheets_pull.py", "Google Forms sync"),
    ("apply_review.py", "Weekly review decision gate"),  # may update the plan, so runs before anything that reads it
    ("race_predictor.py", "Race prediction"),
    ("build_weekly_summary.py", "Weekly summary build"),
    ("build_session_detail.py", "Session detail build"),  # reads garmin_laps.json
    ("build_computed.py", "Computed layer build"),
]

# --forms-only: the fast lane, run the moment a form is submitted.
# Garmin and the weather have nothing new to say seconds after a form
# entry, and Garmin is the slowest step by far — skipping both takes the
# run from ~2 minutes to ~15 seconds, which is the difference between a
# reply that feels immediate and one that doesn't.
FORMS_ONLY_STEPS = [
    ("sheets_pull.py", "Google Forms sync"),
    ("apply_review.py", "Weekly review decision gate"),
    ("build_weekly_summary.py", "Weekly summary build"),
    ("build_computed.py", "Computed layer build"),
]


def run_step(script, label):
    print(f"\n--- {label} ({script}) ---")
    result = subprocess.run(
        [sys.executable, script],
        capture_output=True, text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"FAILED: {label}")
        print(result.stderr)
        return False
    return True


def main():
    forms_only = "--forms-only" in sys.argv
    steps = FORMS_ONLY_STEPS if forms_only else STEPS
    mode = " (forms only)" if forms_only else ""
    print(f"=== run_all.py started {datetime.now().isoformat()}{mode} ===")
    results = {}
    for script, label in steps:
        results[label] = run_step(script, label)

    print("\n=== Summary ===")
    for label, ok in results.items():
        print(f"{'OK' if ok else 'FAILED'}: {label}")

    if not all(results.values()):
        print("\nOne or more steps failed — check output above.")
        sys.exit(1)

    print("\nAll steps completed.")


if __name__ == "__main__":
    main()
