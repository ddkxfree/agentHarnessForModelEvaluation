import pytest

from harness.core.scorer import ChoiceScorer, NumberMatchScorer, normalize_number
from harness.core.types import Sample


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234", "1234"),
        ("$18.00", "18"),
        ("-3", "-3"),
        ("0.5", "0.5"),
        ("0.50", "0.5"),
        ("7.", "7"),
        ("-0", "0"),
        ("abc", None),
        ("nan", None),
        ("NaN", None),
        ("inf", None),
        ("-Infinity", None),
        ("", None),
        ("10000000000000000000000001", "10000000000000000000000001"),
    ],
)
def test_normalize_number(raw, expected):
    assert normalize_number(raw) == expected


def test_large_integers_are_compared_exactly():
    sample = Sample(id="x", input="", target="10000000000000000000000001")
    assert NumberMatchScorer()(sample, "The answer is 10000000000000000000000000").value == 0.0
    assert NumberMatchScorer()(sample, "The answer is 10000000000000000000000001").value == 1.0


@pytest.mark.parametrize("target", ["nan", "inf", "", "seven"])
def test_non_numeric_target_is_a_scorer_error_not_a_wrong_answer(target):
    with pytest.raises(ValueError, match="is not a number"):
        NumberMatchScorer()(Sample(id="x", input="", target=target), "The answer is 7")


@pytest.mark.parametrize("target", ["E", "AB", ""])
def test_choice_target_outside_letters_is_rejected(target):
    with pytest.raises(ValueError, match="is not one of"):
        ChoiceScorer()(Sample(id="x", input="", target=target), "The answer is A")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.5])
def test_score_value_must_be_finite_and_in_range(value):
    from pydantic import ValidationError

    from harness.core.types import Score

    with pytest.raises(ValidationError):
        Score(value=value)


@pytest.mark.parametrize(
    ("text", "extracted", "method"),
    [
        ("3 + 4 = 7.\nThe answer is 18.", "18", "answer_is"),
        ("so the answer is: **$1,200**", "1200", "answer_is"),
        ("work...\n#### 42", "42", "hash_marker"),
        (r"Thus \boxed{36}.", "36", "boxed"),
        ("She has 5 apples and then 9.", "9", "last_number"),
        ("I don't know.", None, None),
    ],
)
def test_number_extraction(text, extracted, method):
    assert NumberMatchScorer().extract(text) == (extracted, method)


def test_number_scorer_no_fallback_reports_extract_failure():
    score = NumberMatchScorer(fallback_last_number=False)(
        Sample(id="x", input="", target="9"), "She has 5 apples and then 9."
    )
    assert score.value == 0.0
    assert score.extracted is None


def test_number_scorer_matches_numerically():
    score = NumberMatchScorer()(Sample(id="x", input="", target="1,200"), "The answer is 1200.00")
    assert score.value == 1.0


@pytest.mark.parametrize(
    ("text", "extracted", "method"),
    [
        ("The answer is C.", "C", "answer_is"),
        ("Reasoning... the answer is (B)", "B", "answer_is"),
        ("D", "D", "only_letter"),
        ("(A)", "A", "only_letter"),
        ("B. Because the mitochondria...", "B", "leading_letter"),
        ("I think option C is right", "C", "last_letter"),
        ("The answer is a bit unclear", None, None),
        ("no idea", None, None),
    ],
)
def test_choice_extraction(text, extracted, method):
    assert ChoiceScorer().extract(text) == (extracted, method)


def test_describe_fingerprint_changes_with_config():
    a = NumberMatchScorer().describe()["fingerprint"]
    b = NumberMatchScorer(fallback_last_number=False).describe()["fingerprint"]
    assert a != b
    assert NumberMatchScorer().describe()["normalization"] == "decimal_exact"
