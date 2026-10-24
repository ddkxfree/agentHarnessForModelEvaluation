#!/bin/bash
set -u
cd /app
python - <<'EOF'
import math
import sys

try:
    from stats import mean, median
except Exception as exc:
    print(f"FAIL: cannot import stats: {exc}")
    sys.exit(1)

cases = [
    (lambda: mean([1, 2, 3]), 2),
    (lambda: mean([5]), 5),
    (lambda: mean([1.5, 2.5]), 2.0),
    (lambda: mean([-2, 2, 6]), 2),
    (lambda: median([3, 1, 2]), 2),
    (lambda: median([4, 1, 3, 2]), 2.5),
]
for i, (fn, expected) in enumerate(cases):
    try:
        got = fn()
    except Exception as exc:
        print(f"FAIL: case {i} raised {exc!r}")
        sys.exit(1)
    # NaN compares false with everything, so check type and finiteness before the tolerance.
    if isinstance(got, bool) or not isinstance(got, (int, float)):
        print(f"FAIL: case {i} returned {type(got).__name__}, not a number")
        sys.exit(1)
    if not math.isfinite(got):
        print(f"FAIL: case {i} returned non-finite {got!r}")
        sys.exit(1)
    if abs(got - expected) > 1e-9:
        print(f"FAIL: case {i} expected {expected}, got {got}")
        sys.exit(1)
print("PASS")
EOF
