from otomasyon.eligibility import (
    competition_exclusion_reason,
    is_daily_eligible,
    is_event_eligible,
    team_exclusion_reason,
)


def test_friendlies_are_excluded_in_turkish_and_english():
    assert not is_daily_eligible("Kulüplerarası Hazırlık Maçlar")
    assert not is_daily_eligible("International Friendlies")
    assert "hazirlik" in competition_exclusion_reason("Hazırlık Maçı")


def test_youth_and_reserve_competitions_are_excluded():
    assert not is_daily_eligible("İngiltere U21 Premier Lig")
    assert not is_daily_eligible("Reserve League")
    assert not is_daily_eligible("UEFA Youth League")


def test_amateur_and_regional_tiers_are_excluded():
    assert not is_daily_eligible("İngiltere Amatör İngiltere Güney Ligi, Merkez")
    assert not is_daily_eligible("Almanya Amatör Bölgesel Lig Kuzey")
    assert not is_daily_eligible("Amateur Regional League")


def test_regular_and_womens_competitions_remain_eligible():
    assert is_daily_eligible("Belçika Pro Lig")
    assert is_daily_eligible("UEFA Şampiyonlar Ligi, Kadınlar")


def test_reserve_team_suffixes_are_excluded_inside_official_leagues():
    assert not is_event_eligible("Norveç 3. Lig", "Foerde", "Brann 2")
    assert not is_event_eligible("Almanya 3. Lig", "Hoffenheim II", "Rostock")
    assert not is_event_eligible("Premier Lig", "Chelsea Academy", "Arsenal")
    assert is_event_eligible("Bundesliga", "Schalke 04", "Bayern Münih")


def test_dutch_reserve_sides_are_excluded():
    """"Jong <club>" sides carry no token the suffix rules would catch."""
    for name in ("Jong AZ Alkmaar", "Jong PSV", "Jong Utrecht"):
        assert team_exclusion_reason(name) is not None
        assert not is_event_eligible("Hollanda 1. Lig", name, "Eindhoven")


def test_senior_clubs_named_like_reserves_are_kept():
    """Willem II plays in the Eredivisie; the suffix rule used to drop it."""
    assert team_exclusion_reason("Willem II") is None
    assert team_exclusion_reason("Juan Pablo II") is None
    assert is_event_eligible("Hollanda Eredivisie", "Willem II", "Ajax")


def test_reserve_suffixes_still_excluded():
    for name in ("Porto B", "Schalke 04 II", "Hertha Berlin II"):
        assert team_exclusion_reason(name) is not None


def test_sub_age_groups_are_excluded():
    assert team_exclusion_reason("Benfica Sub-23") is not None
