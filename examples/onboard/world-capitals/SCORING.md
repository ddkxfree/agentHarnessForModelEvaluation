# Official scoring rule

An answer is correct when, after normalization, it equals the normalized `capital` or one of
the normalized `alt_names`. Otherwise it is wrong. Score 1 for correct, 0 for wrong.

Normalization, in this order:

1. Unicode NFKD, then drop combining marks (so `Brasília` becomes `Brasilia`).
2. Lower-case.
3. Strip surrounding whitespace, then one trailing `.` if present, then whitespace again.

An empty answer is wrong. An answer that contains the capital inside a longer sentence
(e.g. `The capital is Paris`) is wrong: the task asks for the city name only.
