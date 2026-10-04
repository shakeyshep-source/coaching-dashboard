"""
test_parse_distance.py - the race form's free-text Distance box.

Run with: python3 test_parse_distance.py

Sharpness 4 (4 Oct 2026) was entered in miles, parsed as None, and the
predictor dropped the race without a word. Club racing here is mostly
in miles, so these pin down how he actually writes distances.
"""

import sys

from sheets_pull import parse_distance_km, MILE_KM

failures = []


def check(raw, want):
    got = parse_distance_km(raw)
    ok = (got is None and want is None) or (
        got is not None and want is not None and abs(got - want) < 0.001)
    if not ok:
        failures.append(f"{raw!r}: got {got!r}, want {want!r}")


# Bare numbers stay kilometres - the existing history depends on it.
check("21.0975", 21.0975)
check("10", 10.0)
check("5.02", 5.02)
check(" 14.59 ", 14.59)

# Kilometres, however spelt.
check("10k", 10.0)
check("10K", 10.0)
check("10 km", 10.0)
check("5 kilometres", 5.0)

# Miles - the case that broke.
check("4 miles", 4 * MILE_KM)
check("4 mile", 4 * MILE_KM)
check("4 Miles", 4 * MILE_KM)
check("4mi", 4 * MILE_KM)
check("5m", 5 * MILE_KM)
check("10 miles", 10 * MILE_KM)
check("4 mile road race", 4 * MILE_KM)

# Metres: a bare "m" from 100 up is a track distance, not miles.
check("1500m", 1.5)
check("3000 metres", 3.0)

# Named distances.
check("half marathon", 21.0975)
check("Half Marathon", 21.0975)
check("marathon", 42.195)
check("Mile", MILE_KM)

# Nothing usable.
check("", None)
check(None, None)
check("XC", None)

if failures:
    print(f"{len(failures)} FAILED:")
    print("\n".join(failures))
    sys.exit(1)
print("all distance tests passed")
