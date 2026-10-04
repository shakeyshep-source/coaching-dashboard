"""
test_race_predictor.py - how races are weighted into a prediction.

Run with: python3 test_race_predictor.py

Until 4 Oct 2026 every clean race counted equally for every distance,
and the half-marathon prediction sat a minute slower than a half he had
run a fortnight earlier. These pin the weighting that replaced it.
"""

import sys
from datetime import date

import race_predictor as rp

failures = []


def check(label, ok, detail=""):
    if not ok:
        failures.append(f"{label} {detail}")


TODAY = date(2026, 10, 4)
half = {"name": "Half", "date": "2026-09-20", "distance_km": 21.0975, "time_seconds": 4863}
five_k = {"name": "5K", "date": "2026-09-20", "distance_km": 5.0, "time_seconds": 1068}
old_half = {"name": "Old half", "date": "2026-06-22", "distance_km": 21.0975, "time_seconds": 4863}

# A race at the target distance outweighs one projected from a quarter of it.
w_same = rp.race_weight(half, 21.0975, TODAY)
w_far = rp.race_weight(five_k, 21.0975, TODAY)
check("same distance outweighs a 5K for a half", w_same > 3 * w_far, f"{w_same:.0f} vs {w_far:.0f}")

# ...and symmetrically, the 5K outweighs the half when predicting a 5K.
check("5K outweighs the half for a 5K",
      rp.race_weight(five_k, 5.0, TODAY) > 3 * rp.race_weight(half, 5.0, TODAY))

# Age halves the weight every RECENCY_HALF_LIFE_DAYS.
ratio = rp.race_weight(old_half, 21.0975, TODAY) / w_same
check("90 days older halves the weight", abs(ratio - 0.5) < 0.01, f"ratio {ratio:.3f}")

# Weights are positive and finite for anything sensible.
for r in (half, five_k, old_half):
    for t in (1.6, 5, 10, 21.0975, 42.195):
        w = rp.race_weight(r, t, TODAY)
        check(f"weight positive {r['name']}->{t}", w > 0 and w < 1e9)

# A missing or bad date counts as today rather than crashing.
check("bad date counts as age 0",
      rp.race_weight({**half, "date": "nope"}, 21.0975, TODAY)
      == rp.race_weight({**half, "date": TODAY.isoformat()}, 21.0975, TODAY))

# The prediction sits between the projections and nearer the trusted one.
pred = rp.predict_for_distance([half, five_k], 21.0975, None, None, TODAY)
p5 = rp.riegel_predict(1068, 5.0, 21.0975)
central = pred["predicted_time_seconds"]
check("prediction between projections", min(4863, p5) <= central <= max(4863, p5), f"{central}")
check("prediction nearer the actual half", abs(central - 4863) < abs(central - p5), f"{central}")
check("note names the race carrying the weight", "Half carries" in pred["note"], pred["note"])

# A single race predicts itself at its own distance.
solo = rp.predict_for_distance([half], 21.0975, None, None, TODAY)
check("single race predicts itself", abs(solo["predicted_time_seconds"] - 4863) < 0.5)

# The exponent was deliberately left at the standard value.
check("exponent unchanged", rp.RIEGEL_EXPONENT == 1.06)

if failures:
    print(f"{len(failures)} FAILED:")
    print("\n".join(failures))
    sys.exit(1)
print("all predictor weighting tests passed")
