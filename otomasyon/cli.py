"""Command line entry points for Otomasyon.

Usage:
    python -m otomasyon.cli fetch [--sample N]  # fetch & store today's bulletin
    python -m otomasyon.cli coupon [--no-save]  # today's low-risk coupons
    python -m otomasyon.cli surprise            # surprise-lab report
    python -m otomasyon.cli bot                 # run the Telegram bot
"""

from __future__ import annotations

import argparse
from datetime import datetime

from . import config, engine, formatting, probability, service, surprise
from .storage import Database


def cmd_fetch(args: argparse.Namespace) -> int:
    print("Pazar konfigürasyonu + ligler + bülten çekiliyor...")
    events, competitions = service.fetch_normalized_events()
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


def cmd_coupon(args: argparse.Namespace) -> int:
    print("Bülten çekiliyor...")
    text = service.daily_text(args.db, save=not args.no_save)
    print("\n" + text)
    if not args.no_save:
        print("\n(Kuponlar takip için veritabanına kaydedildi.)")
    return 0


def cmd_surprise(args: argparse.Namespace) -> int:
    print("Bülten çekiliyor...")
    print("\n" + service.surprise_text())
    return 0


def cmd_bot(args: argparse.Namespace) -> int:
    from .telegram import Bot, TelegramClient

    username = config.telegram_allowed_username()
    if not username:
        print("HATA: TELEGRAM_ALLOWED_USERNAME tanımlı değil.")
        return 1
    client = TelegramClient()
    me = client.get_me()
    print(
        f"Bot bağlandı: @{me.get('username')}  | izinli kullanıcı: @{username}  "
        f"| günlük proaktif gönderim saati: {config.DAILY_PUSH_HOUR}:00"
    )

    db = Database(args.db)
    bot = Bot(
        client,
        username,
        on_daily=lambda: service.daily_text(args.db),
        on_surprise=service.surprise_text,
        db=db,
        push_hour=config.DAILY_PUSH_HOUR,
        push_callback=lambda: service.push_daily(args.db, client),
    )
    bot.run()
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    from .telegram import TelegramClient

    client = TelegramClient()
    chat_id = service.push_daily(args.db, client, force=args.force)
    if chat_id:
        print(f"Günün kuponu proaktif olarak gönderildi -> chat {chat_id}")
    else:
        print("Gönderim atlandı (chat_id yok ya da bugün zaten gönderildi).")
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
        for code in (config.MARKET_MATCH_RESULT, config.MARKET_OVER_UNDER):
            mk = e.market(code)
            if not mk:
                continue
            fps = probability.fair_probs(mk.odds)
            parts = [
                f"{s.name}={s.odd} (%{fp * 100:.0f})"
                for s, fp in zip(mk.selections, fps)
            ]
            print(f"  {mk.name}: " + "  ".join(parts))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="otomasyon", description=__doc__)
    parser.add_argument("--db", default=config.DB_PATH, help="SQLite path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="Fetch and store today's football bulletin")
    p_fetch.add_argument("--sample", type=int, default=0, help="Print N sample events")
    p_fetch.set_defaults(func=cmd_fetch)

    p_coupon = sub.add_parser("coupon", help="Build today's low-risk coupons")
    p_coupon.add_argument("--no-save", action="store_true", help="Do not persist")
    p_coupon.set_defaults(func=cmd_coupon)

    p_surprise = sub.add_parser("surprise", help="Build the surprise-lab report")
    p_surprise.set_defaults(func=cmd_surprise)

    p_bot = sub.add_parser("bot", help="Run the single-user Telegram bot")
    p_bot.set_defaults(func=cmd_bot)

    p_push = sub.add_parser("push", help="Proactively push today's coupon now")
    p_push.add_argument(
        "--force", action="store_true", help="Send even if already pushed today"
    )
    p_push.set_defaults(func=cmd_push)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
