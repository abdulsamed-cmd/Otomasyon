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
        result_callback=lambda: service.auto_results(args.db, client),
    )
    bot.run()
    return 0


def _parse_score(text: str) -> tuple[int, int]:
    home, away = text.replace(":", "-").split("-")
    return int(home), int(away)


def cmd_result(args: argparse.Namespace) -> int:
    from .settlement import MatchResult

    ft_home = ft_away = ht_home = ht_away = None
    if args.ft:
        ft_home, ft_away = _parse_score(args.ft)
    if args.ht:
        ht_home, ht_away = _parse_score(args.ht)
    result = MatchResult(
        event_id=args.event, ft_home=ft_home, ft_away=ft_away,
        ht_home=ht_home, ht_away=ht_away, status=args.status,
    )
    service.record_result(args.db, result)
    print(f"Sonuç kaydedildi: event {args.event} = {args.status} {args.ft or ''}")
    return 0


def cmd_settle(args: argparse.Namespace) -> int:
    client = None
    if args.notify:
        from .telegram import TelegramClient

        client = TelegramClient()
    decided = service.settle_pending(args.db, client, notify=args.notify)
    if not decided:
        print("Kapatılacak (sonucu gelmiş) bekleyen kupon yok.")
        return 0
    for item in decided:
        print("\n" + formatting.format_settlement(item["coupon"], item["settlement"]))
    if args.notify:
        print(f"\n({len(decided)} kupon sonucu Telegram'dan bildirildi.)")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    m = service.metrics(args.db)
    print("=== Performans metrikleri (düz 1 birim bahis) ===")
    print(f"  Sonuçlanan kupon : {m['coupons_played']} (toplam {m['coupons_total']})")
    print(f"  Tutan            : {m['won']}")
    print(f"  İsabet oranı     : %{m['hit_rate'] * 100:.1f}")
    print(f"  Ortalama oran    : {m['avg_odds']:.2f}")
    print(f"  Kâr/Zarar        : {m['profit']:+.2f} birim")
    print(f"  ROI              : %{m['roi'] * 100:.1f}")
    return 0


def cmd_auto_results(args: argparse.Namespace) -> int:
    client = None
    if args.notify:
        from .telegram import TelegramClient

        client = TelegramClient()
    report = service.auto_results(
        args.db, client, force=args.force
    )
    if report.get("skipped"):
        print(f"Sonuç taraması atlandı: {report.get('reason')}")
        return 0
    print(
        f"Sonuç taraması: {report['matched']} maç eşleşti, "
        f"{report['settled']} kupon kapandı"
    )
    if report.get("dates"):
        print("Taranan tarihler:", ", ".join(report["dates"]))
    for item in report.get("diagnostics", []):
        if item["method"] != "unmatched":
            print(
                f"  event {item['event_id']}: {item['method']} "
                f"(güven {item['score']:.3f})"
            )
    return 0


def cmd_history_backfill(args: argparse.Namespace) -> int:
    report = service.backfill_history(args.db, days=args.days)
    print(
        f"Tarihsel veri: {report['days']} gün, "
        f"{report['rows_processed']} satır işlendi, DB toplam {report['total']}"
    )
    if report["skipped_days"]:
        print(f"Önceden mevcut gün (atlandı): {report['skipped_days']}")
    if report["errors"]:
        print(f"Hatalı gün sayısı: {len(report['errors'])}")
        for error in report["errors"][:5]:
            print(f"  {error['date']}: {error['error']}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from .model import backtest

    with Database(args.db) as db:
        history = db.load_historical_matches()
    report = backtest(
        history, test_days=args.test_days, test_end_date=args.test_end
    )
    if report.get("error"):
        print("Backtest yapılamadı:", report["error"])
        return 1
    print("=== Kronolojik model backtest (rapor modu) ===")
    print(f"  Eğitim maçı     : {report['train_matches']}")
    print(f"  Test maçı       : {report['test_matches']}")
    print(f"  Değer seçimi    : {report['bets']}")
    print(f"  Kazanan         : {report['wins']}")
    print(f"  İsabet          : %{report['hit_rate'] * 100:.1f}")
    print(f"  Ortalama oran   : {report['avg_odds']:.2f}")
    print(f"  Ortalama edge   : %{report['avg_edge'] * 100:.1f}")
    print(f"  ROI             : %{report['roi'] * 100:.1f}")
    lo, hi = report["roi_ci95"]
    print(f"  ROI %95 aralık  : %{lo * 100:.1f} .. %{hi * 100:.1f}")
    print(f"  1X2 Brier       : {report['brier_1x2']:.4f}")
    print(
        f"  Piyasa favorisi : {report['market_favourite_bets']} seçim, "
        f"ROI %{report['market_favourite_roi'] * 100:.1f}"
    )
    for market, values in report["market_breakdown"].items():
        print(
            f"  {market:15}: {values['bets']} seçim, "
            f"{values['wins']} kazanan, ROI %{values['roi'] * 100:.1f}"
        )
    print(
        "  Kabul kapısı     : "
        + ("GEÇTİ" if report["gate_passed"] else "KALDI (canlıya alınamaz)")
    )
    print("  Canlı model     : KAPALI (kanıt kapısı)")
    return 0


def cmd_clubelo(args: argparse.Namespace) -> int:
    from .clubelo import ClubEloClient, compare_to_iddaa

    events, _ = service.get_live_events()
    fixtures = ClubEloClient().fetch_fixtures()
    report = compare_to_iddaa(events, fixtures)
    print("=== ClubElo bağımsız görüş (RAPOR MODU) ===")
    print(
        f"ClubElo fikstürü {report['clubelo_fixtures']}, "
        f"iddaa eşleşmesi {report['matched']}/{report['events']}"
    )
    for item in report["reports"][: args.limit]:
        print(
            f"  {item['home']} - {item['away']} | {item['outcome']} "
            f"@ {item['odd']} | iddaa %{item['iddaa_fair']*100:.1f}, "
            f"ClubElo %{item['clubelo_prob']*100:.1f}, "
            f"fark {item['difference']*100:+.1f} puan"
        )
    print("Canlı seçim etkisi: KAPALI (tarihsel doğrulama yok)")
    return 0


def cmd_clubelo_backfill(args: argparse.Namespace) -> int:
    report = service.backfill_clubelo(
        args.db,
        start_date=datetime.strptime(args.start, "%Y-%m-%d").date(),
        end_date=datetime.strptime(args.end, "%Y-%m-%d").date(),
    )
    print(
        f"ClubElo: {report['fetched']} gün çekildi, "
        f"{report['skipped']} gün atlandı, {report['rows']} rating kaydedildi"
    )
    for error in report["errors"]:
        print(f"  {error['date']}: {error['error']}")
    return 0 if not report["errors"] else 1


def cmd_clubelo_backtest(args: argparse.Namespace) -> int:
    from .clubelo import backtest_ratings

    with Database(args.db) as db:
        history = db.load_historical_matches()
        ratings = db.load_clubelo_ratings(args.start, args.end)
    report = backtest_ratings(
        history,
        ratings,
        start_date=args.start,
        end_date=args.end,
        min_edge=args.min_edge,
    )
    print("=== ClubElo sabit dönem backtest (RAPOR MODU) ===")
    print(
        f"Test {report['test_matches']}, kapsanan {report['covered']}, "
        f"seçim {report['bets']}, kazanan {report['wins']}"
    )
    print(
        f"ROI %{report['roi']*100:.1f} "
        f"(%95: %{report['roi_ci95'][0]*100:.1f} .. "
        f"%{report['roi_ci95'][1]*100:.1f}), "
        f"ort. oran {report['avg_odds']:.2f}, "
        f"ort. edge %{report['avg_edge']*100:.1f}"
    )
    print(
        "Kabul kapısı: "
        + ("GEÇTİ" if report["gate_passed"] else "KALDI (canlıya alınamaz)")
    )
    return 0


def cmd_coupon_replay(args: argparse.Namespace) -> int:
    from .replay import replay_daily

    with Database(args.db) as db:
        history = db.load_historical_matches()
    report = replay_daily(
        history,
        start_date=args.start,
        end_date=args.end,
        generation_hour=args.hour,
        calibrated=args.calibrated,
        calibration_prior=args.calibration_prior,
        min_expected_value=args.min_ev,
    )
    print("=== Günlük kupon motoru tarihsel replay ===")
    print(
        "Yöntem: kapanış oranı / kısmi pazar "
        "(1X2 + Alt/Üst 2.5; 10:00 anlık oranı değildir)"
    )
    print(
        "Olasılık: "
        + (
            f"geçmiş dönem kalibrasyonu (prior={args.calibration_prior:g})"
            if args.calibrated
            else "piyasa marjı arındırılmış"
        )
    )
    for label, key in (
        ("Ana", "main"),
        ("Alternatif", "alternative"),
        ("Toplam", "combined"),
    ):
        values = report[key]
        print(
            f"{label:11}: {values['coupons']} kupon, "
            f"{values['won']} tuttu/{values['lost']} yatmadı, "
            f"isabet %{values['hit_rate']*100:.1f}, "
            f"ort. oran {values['average_odds']:.2f}, "
            f"ROI %{values['roi']*100:.1f}"
        )
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

    p_result = sub.add_parser("result", help="Record a match result (event_id + scores)")
    p_result.add_argument("--event", type=int, required=True, help="iddaa event id")
    p_result.add_argument("--ft", help="Full-time score, e.g. 2-1")
    p_result.add_argument("--ht", help="Half-time score, e.g. 1-0")
    p_result.add_argument(
        "--status", default="final", choices=["final", "postponed", "cancelled"]
    )
    p_result.set_defaults(func=cmd_result)

    p_settle = sub.add_parser("settle", help="Settle pending coupons whose results are in")
    p_settle.add_argument(
        "--notify", action="store_true", help="Send result notifications to Telegram"
    )
    p_settle.set_defaults(func=cmd_settle)

    p_metrics = sub.add_parser("metrics", help="Show performance metrics")
    p_metrics.set_defaults(func=cmd_metrics)

    p_auto = sub.add_parser(
        "auto-results", help="Fetch Mackolik results, settle and optionally notify"
    )
    p_auto.add_argument("--force", action="store_true", help="Ignore poll interval")
    p_auto.add_argument("--notify", action="store_true", help="Notify Telegram")
    p_auto.set_defaults(func=cmd_auto_results)

    p_history = sub.add_parser(
        "history-backfill", help="Backfill Mackolik history for model training"
    )
    p_history.add_argument("--days", type=int, default=90)
    p_history.set_defaults(func=cmd_history_backfill)

    p_backtest = sub.add_parser(
        "backtest", help="Run chronological contextual-model backtest"
    )
    p_backtest.add_argument("--test-days", type=int, default=14)
    p_backtest.add_argument(
        "--test-end", help="Inclusive holdout end date (YYYY-MM-DD)"
    )
    p_backtest.set_defaults(func=cmd_backtest)

    p_clubelo = sub.add_parser(
        "clubelo", help="Compare current iddaa 1X2 with independent ClubElo"
    )
    p_clubelo.add_argument("--limit", type=int, default=10)
    p_clubelo.set_defaults(func=cmd_clubelo)

    p_ce_backfill = sub.add_parser(
        "clubelo-backfill", help="Cache daily historical ClubElo ratings"
    )
    p_ce_backfill.add_argument("--start", required=True)
    p_ce_backfill.add_argument("--end", required=True)
    p_ce_backfill.set_defaults(func=cmd_clubelo_backfill)

    p_ce_test = sub.add_parser(
        "clubelo-backtest", help="Backtest cached ClubElo ratings"
    )
    p_ce_test.add_argument("--start", required=True)
    p_ce_test.add_argument("--end", required=True)
    p_ce_test.add_argument("--min-edge", type=float, default=config.MODEL_MIN_EDGE)
    p_ce_test.set_defaults(func=cmd_clubelo_backtest)

    p_replay = sub.add_parser(
        "coupon-replay", help="Replay production daily coupons on historical odds"
    )
    p_replay.add_argument("--start", required=True)
    p_replay.add_argument("--end", required=True)
    p_replay.add_argument("--hour", type=int, default=config.DAILY_PUSH_HOUR)
    p_replay.add_argument("--calibrated", action="store_true")
    p_replay.add_argument("--calibration-prior", type=float, default=100.0)
    p_replay.add_argument(
        "--min-ev",
        type=float,
        help="Minimum estimated coupon EV (e.g. 0 for non-negative)",
    )
    p_replay.set_defaults(func=cmd_coupon_replay)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
