from otomasyon.eligibility import (
    competition_exclusion_reason,
    is_daily_eligible,
)


def test_friendlies_are_excluded_in_turkish_and_english():
    assert not is_daily_eligible("Kulüplerarası Hazırlık Maçlar")
    assert not is_daily_eligible("International Friendlies")
    assert "hazirlik" in competition_exclusion_reason("Hazırlık Maçı")


def test_youth_and_reserve_competitions_are_excluded():
    assert not is_daily_eligible("İngiltere U21 Premier Lig")
    assert not is_daily_eligible("Reserve League")
    assert not is_daily_eligible("UEFA Youth League")


def test_regular_and_womens_competitions_remain_eligible():
    assert is_daily_eligible("Belçika Pro Lig")
    assert is_daily_eligible("UEFA Şampiyonlar Ligi, Kadınlar")
