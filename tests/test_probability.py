import math

import pytest

from otomasyon import probability as p


def test_implied_prob_basic():
    assert p.implied_prob(2.0) == pytest.approx(0.5)
    assert p.implied_prob(4.0) == pytest.approx(0.25)


def test_implied_prob_rejects_invalid():
    with pytest.raises(ValueError):
        p.implied_prob(1.0)
    with pytest.raises(ValueError):
        p.implied_prob(0.5)


def test_fair_probs_sum_to_one():
    # A 1X2 market with margin: raw implied sums to > 1.
    odds = [1.91, 3.40, 4.20]
    fair = p.fair_probs(odds)
    assert math.isclose(sum(fair), 1.0, rel_tol=1e-9)
    # favourite keeps the highest probability
    assert fair[0] > fair[1] > fair[2]


def test_fair_probs_removes_overround():
    odds = [1.5, 2.5]  # implied 0.6667 + 0.4 = 1.0667 overround
    assert p.market_overround(odds) == pytest.approx(1.0 / 1.5 + 1.0 / 2.5)
    fair = p.fair_probs(odds)
    assert sum(fair) == pytest.approx(1.0)
    assert fair[0] == pytest.approx((1 / 1.5) / (1 / 1.5 + 1 / 2.5))


def test_total_odds_and_combined_probability():
    assert p.total_odds([1.5, 1.4, 1.3]) == pytest.approx(1.5 * 1.4 * 1.3)
    assert p.combined_probability([0.8, 0.75]) == pytest.approx(0.6)


def test_fair_prob_for_index():
    odds = [1.5, 2.5]
    assert p.fair_prob_for(odds, 0) == pytest.approx(p.fair_probs(odds)[0])
