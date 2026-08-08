"""Command line entry points for Otomasyon.

Usage:
    python -m otomasyon.cli fetch            # fetch today's football bulletin
    python -m otomasyon.cli fetch --sample 3 # ...and print 3 sample events
"""

from __future__ import annotations

import argparse
from datetime import datetime

from . import config, engine, probability
from .iddaa import IddaaClient, MarketResolver, normalize_events
from .iddaa.normalize import build_competitions_map
from .storage import Database


def fetch_normalized_events(client: IddaaClient | None = None):
    """Fetch competitions + bulletin and return (events, competitions)."""
    client = client or IddaaClient()
    resolver = MarketResolver.from_client(client)
    competitions = build_competitions_map(client.get_competitions())
    raw_events = client.get_events()
    events = normalize_events(raw_events, resolver, competitions)
    return events, competitions


def cmd_fetch(args: argparse.Namespace) -> int:
    print("Pazar konfigürasyonu + ligler + bülten çekiliyor...")
    events, competitions = fetch_normalized_events()
    print(f"  -> {len(events)} maç, {len(competitions)} lig")

    with Database(args.db) as db:
        db.upsert_competitions(competitions)
        stats = db.save_events(events)
    print(
        "Kaydedildi: "
        f"{stats['events']} maç, {stats['markets']} pazar, "
        f"{stats['selections']} seçim, {stats['odds']} oran anlık kaydı"
    )

    if args.sample:
        _print_samples(events, args.sample)
    return 0


def _print_coupon(title: str, coupon) -> None:
    if coupon is None:
        print(f"\n{title}: uygun kupon bulunamadı.")
        return
    print(
        f"\n{title}  (toplam oran {coupon.total_odds:.2f}, "
        f"birleşik olasılık %{coupon.combined_prob * 100:.1f}, "
        f"{len(coupon.legs)} maç)"
    )
    for leg in coupon.legs:
        when = datetime.fromtimestamp(leg.start_ts, tz=config.TIMEZONE).strftime(
            "%H:%M"
        )
        print(
            f"  [{when}] {leg.home} - {leg.away}  ({leg.competition})\n"
            f"         {leg.market_name}: {leg.outcome_name} @ {leg.odd}  "
            f"(adil %{leg.fair_prob * 100:.0f})"
        )


def cmd_coupon(args: argparse.Namespace) -> int:
    print("Bülten çekiliyor...")
    events, competitions = fetch_normalized_events()
    print(f"  -> {len(events)} maç")

    now = datetime.now(tz=config.TIMEZONE)
    coupons = engine.build_daily_coupons(events, now=now)
    for_date = now.strftime("%Y-%m-%d")

    print(f"\n=== Günün düşük riskli kuponları ({for_date}) ===")
    _print_coupon("ANA KUPON", coupons["main"])
    _print_coupon("ALTERNATİF", coupons["alt"])

    if not args.no_save and (coupons["main"] or coupons["alt"]):
        with Database(args.db) as db:
            db.upsert_competitions(competitions)
            db.save_events(events)
            for c in (coupons["main"], coupons["alt"]):
                if c:
                    db.save_coupon(c, for_date)
        print("\n(Kuponlar takip için veritabanına kaydedildi.)")
    return 0


def _print_samples(events, n: int) -> None:
    upcoming = sorted(
        (e for e in events if e.market(config.MARKET_MATCH_RESULT)),
        key=lambda e: e.start_ts,
    )[:n]
    print("\n=== Örnek maçlar (adil olasılıklarla) ===")
    for e in upcoming:
        when = datetime.fromtimestamp(e.start_ts, tz=config.TIMEZONE).strftime(
            "%d.%m %H:%M"
        )
        print(f"\n[{when}] {e.home} - {e.away}  ({e.competition_name})")
        ms = e.market(config.MARKET_MATCH_RESULT)
        if ms:
            fps = probability.fair_probs(ms.odds)
            parts = [
                f"{s.name}={s.odd} (%{fp * 100:.0f})"
                for s, fp in zip(ms.selections, fps)
            ]
            print("  Maç Sonucu: " + "  ".join(parts))
        ou = e.market(config.MARKET_OVER_UNDER)
        if ou:
            fps = probability.fair_probs(ou.odds)
            parts = [
                f"{s.name}={s.odd} (%{fp * 100:.0f})"
                for s, fp in zip(ou.selections, fps)
            ]
            print(f"  {ou.name}: " + "  ".join(parts))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="otomasyon", description=__doc__)
    parser.add_argument("--db", default=config.DB_PATH, help="SQLite path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="Fetch and store today's football bulletin")
    p_fetch.add_argument(
        "--sample", type=int, default=0, help="Print N sample events after fetching"
    )
    p_fetch.set_defaults(func=cmd_fetch)

    p_coupon = sub.add_parser(
        "coupon", help="Build today's low-risk main + alternative coupons"
    )
    p_coupon.add_argument(
        "--no-save", action="store_true", help="Do not persist coupons to the DB"
    )
    p_coupon.set_defaults(func=cmd_coupon)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
