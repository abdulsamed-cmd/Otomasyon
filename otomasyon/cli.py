"""Command line entry points for Otomasyon.

Usage:
    python -m otomasyon.cli fetch            # fetch today's football bulletin
    python -m otomasyon.cli fetch --sample 3 # ...and print 3 sample events
"""

from __future__ import annotations

import argparse
from datetime import datetime

from . import config, probability
from .iddaa import IddaaClient, MarketResolver, normalize_events
from .iddaa.normalize import build_competitions_map
from .storage import Database


def cmd_fetch(args: argparse.Namespace) -> int:
    client = IddaaClient()
    print("Pazar konfigürasyonu çekiliyor...")
    resolver = MarketResolver.from_client(client)

    print("Ligler çekiliyor...")
    competitions = build_competitions_map(client.get_competitions())

    print("Futbol bülteni çekiliyor...")
    raw_events = client.get_events()
    events = normalize_events(raw_events, resolver, competitions)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
