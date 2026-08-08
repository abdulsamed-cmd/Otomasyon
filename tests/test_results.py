from datetime import date

from otomasyon.results.mackolik import MackolikClient, SourceMatch
from otomasyon.results.matcher import (
    match_source_results,
    normalize_team,
    team_similarity,
)


def _source(
    *,
    code=123,
    home="İstanbul Başakşehir FK",
    away="Galatasaray SK",
    start=1_786_197_600,
    state="post",
    substate="fullTime",
    ft=(2, 1),
    ht=(1, 0),
):
    return SourceMatch(
        source_id="mk1",
        iddaa_code=code,
        home=home,
        away=away,
        start_ts=start,
        state=state,
        substate=substate,
        ft_home=ft[0] if ft else None,
        ft_away=ft[1] if ft else None,
        ht_home=ht[0] if ht else None,
        ht_away=ht[1] if ht else None,
    )


def test_parse_mackolik_match():
    raw = {
        "id": "abc",
        "iddaaCode": 3065351,
        "homeTeam": {"name": "Boluspor"},
        "awayTeam": {"name": "Manisa FK"},
        "mstUtc": 1_786_127_400_000,
        "state": "post",
        "substate": "fullTime",
        "score": {
            "home": "1",
            "away": "2",
            "ht": {"home": 1, "away": 2},
        },
    }
    m = MackolikClient._parse(raw)
    assert m.iddaa_code == 3065351
    assert (m.ft_home, m.ft_away) == (1, 2)
    assert (m.ht_home, m.ht_away) == (1, 2)
    assert m.start_ts == 1_786_127_400
    assert m.is_decided


def test_exact_iddaa_code_match_is_preferred():
    events = [
        {"event_id": 123, "home": "Different", "away": "Names", "start_ts": 10}
    ]
    matched, diag = match_source_results(events, [_source(code=123)])
    assert matched[123].ft_home == 2
    assert matched[123].source == "mackolik:mk1"
    assert diag[0]["method"] == "iddaa_code"


def test_fuzzy_fallback_handles_turkish_and_club_suffixes():
    event = {
        "event_id": 456,
        "home": "Istanbul Basaksehir",
        "away": "Galatasaray",
        "start_ts": 1_786_197_600,
    }
    source = _source(code=None)
    matched, diag = match_source_results([event], [source])
    assert 456 in matched
    assert diag[0]["method"] == "fuzzy"
    assert diag[0]["score"] >= 0.84


def test_ambiguous_or_low_confidence_fallback_is_skipped():
    event = {
        "event_id": 456,
        "home": "Totally Different",
        "away": "Other Team",
        "start_ts": 1_786_197_600,
    }
    matched, diag = match_source_results([event], [_source(code=None)])
    assert matched == {}
    assert diag[0]["method"] == "unmatched"


def test_postponed_maps_to_void_status():
    event = {
        "event_id": 123,
        "home": "A",
        "away": "B",
        "start_ts": 1,
    }
    source = _source(
        code=123, home="A", away="B", state="pre", substate="postponed", ft=None
    )
    matched, _ = match_source_results([event], [source])
    assert matched[123].status == "postponed"


def test_team_normalization():
    assert normalize_team("İstanbulspor FK") == "istanbulspor"
    assert team_similarity("Bodø/Glimt", "Bodo Glimt FK") > 0.9
