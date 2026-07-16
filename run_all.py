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
    ("race_predictor.py", "Race prediction"),
    ("sheets_pull.py", "Google Forms sync"),
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
    print(f"=== run_all.py started {datetime.now().isoformat()} ===")
    results = {}
    for script, label in STEPS:
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
