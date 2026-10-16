from harness import EchoModel, Sample, evaluate


def test_evaluate_echo():
    r = evaluate(EchoModel(), [Sample("a", "A"), Sample("a", "b")])
    assert r["total"] == 2 and r["correct"] == 1 and r["accuracy"] == 0.5
