from otomasyon.iddaa.markets import MarketResolver
from otomasyon.iddaa.normalize import build_competitions_map, normalize_events

MARKET_CONFIG = {
    "m": {
        "1_1": {"n": "Maç Sonucu"},
        "2_101": {"n": "Alt/Üst {0}"},
        "2_89": {"n": "Karşılıklı Gol"},
    }
}

RAW_COMPETITIONS = [
    {"i": 134, "n": "İngiltere Ulusal Lig", "cid": "GB"},
]

RAW_EVENTS = [
    {
        "i": 3069509,
        "hn": "Radcliffe FC",
        "an": "Spalding United",
        "sid": 1,
        "ci": 134,
        "d": 1786197600,
        "s": 0,
        "m": [
            {
                "i": 1,
                "t": 1,
                "st": 1,
                "sov": None,
                "s": 1,
                "o": [
                    {"no": 1, "odd": 1.91, "wodd": 1.84, "n": "1"},
                    {"no": 2, "odd": 3.40, "wodd": 3.30, "n": "0"},
                    {"no": 3, "odd": 4.20, "wodd": 4.10, "n": "2"},
                ],
            },
            {
                "i": 2,
                "t": 2,
                "st": 101,
                "sov": "2.5",
                "s": 1,
                "o": [
                    {"no": 1, "odd": 1.80, "wodd": 1.75, "n": "Alt"},
                    {"no": 2, "odd": 2.00, "wodd": 1.95, "n": "Üst"},
                ],
            },
        ],
    }
]


def _resolver():
    return MarketResolver(MARKET_CONFIG)


def test_market_resolver_substitutes_line():
    r = _resolver()
    assert r.name(1, 1) == "Maç Sonucu"
    assert r.name(2, 101, "2.5") == "Alt/Üst 2.5"
    assert r.name(2, 89) == "Karşılıklı Gol"
    assert r.name(9, 9) == "market 9_9"  # unknown falls back gracefully


def test_normalize_events_structure():
    comps = build_competitions_map(RAW_COMPETITIONS)
    events = normalize_events(RAW_EVENTS, _resolver(), comps)
    assert len(events) == 1
    e = events[0]
    assert e.home == "Radcliffe FC"
    assert e.away == "Spalding United"
    assert e.competition_name == "İngiltere Ulusal Lig"
    assert e.country_code == "GB"

    ms = e.market((1, 1))
    assert ms is not None
    assert ms.name == "Maç Sonucu"
    assert ms.odds == [1.91, 3.40, 4.20]

    ou = e.market((2, 101))
    assert ou.name == "Alt/Üst 2.5"
    assert [s.name for s in ou.selections] == ["Alt", "Üst"]


def test_minimum_bet_count_is_read_per_market():
    raw = [
        {
            **RAW_EVENTS[0],
            "mbc": 2,
            "m": [
                {**RAW_EVENTS[0]["m"][0], "mbc": 3},
                {**RAW_EVENTS[0]["m"][1], "mbc": 1},
            ],
        }
    ]
    event = normalize_events(raw, _resolver(), build_competitions_map(RAW_COMPETITIONS))[0]
    assert event.market((1, 1)).mbs == 3
    assert event.market((2, 101)).mbs == 1


def test_a_market_without_its_own_limit_inherits_the_events():
    raw = [{**RAW_EVENTS[0], "mbc": 2, "m": [dict(RAW_EVENTS[0]["m"][0])]}]
    event = normalize_events(raw, _resolver(), build_competitions_map(RAW_COMPETITIONS))[0]
    assert event.market((1, 1)).mbs == 2


def test_a_feed_that_never_mentions_the_limit_leaves_markets_playable_alone():
    event = normalize_events(RAW_EVENTS, _resolver(), build_competitions_map(RAW_COMPETITIONS))[0]
    assert all(market.mbs == 1 for market in event.markets)
