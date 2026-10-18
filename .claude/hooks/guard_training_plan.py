"""
guard_training_plan.py — PreToolUse hook enforcing coaching principle 1.

Only apply_review.py may change training_plan.json, and only after the
athlete approves. Blocks Claude's Edit/Write/MultiEdit on that file
(exit 2 = deny, stderr goes back to Claude). apply_review.py is
unaffected — it runs as a Python process, not through Claude's tools.
"""

import json
import os
import sys

try:
    payload = json.load(sys.stdin)
except ValueError:
    sys.exit(0)

path = (payload.get("tool_input") or {}).get("file_path") or ""
if os.path.basename(path) == "training_plan.json":
    print(
        "training_plan.json is gated: only apply_review.py changes it, after "
        "Shep approves. Write the change to plan_proposal.json instead.",
        file=sys.stderr,
    )
    sys.exit(2)
