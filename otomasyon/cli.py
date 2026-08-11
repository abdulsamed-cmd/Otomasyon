"""Command line entry points for Otomasyon.

Usage:
    python -m otomasyon.cli fetch [--sample N]  # fetch & store today's bulletin
    python -m otomasyon.cli coupon [--no-save]  # today's low-risk coupons
    python -m otomasyon.cli surprise            # surprise-lab report
    python -m otomasyon.cli bot                 # run the Telegram bot
    python -m otomasyon.cli scheduler           # run scheduled background work
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
    text = service.daily_text(
        args.db, save=not args.no_save, rebuild=args.rebuild
    )
    print("\n" + text)
    if not args.no_save:
        print("\n(Kuponlar takip için veritabanına kaydedildi.)")
    return 0


def cmd_surprise(args: argparse.Namespace) -> int:
    print("Bülten çekiliyor...")
    print("\n" + service.surprise_text(args.db))
    return 0


def _run_bot_once(db_path: str, username: str) -> None:
    """One bot lifetime: a fresh client and database handle, then poll."""
    from .telegram import Bot, TelegramClient

    client = TelegramClient()
    me = client.get_me()
    print(
        f"Bot bağlandı: @{me.get('username')}  | izinli kullanıcı: @{username}"
    )
    with Database(db_path) as db:
        Bot(
            client,
            username,
            on_daily=lambda: service.daily_text(db_path),
            on_surprise=lambda: service.surprise_text(db_path),
            on_lineup=lambda: service.lineup_risk_text(db_path),
            on_status=lambda: service.model_status_text(db_path),
            db=db,
        ).run()


def cmd_bot(args: argparse.Namespace) -> int:
    from .telegram import supervise

    username = config.telegram_allowed_username()
    if not username:
        print("HATA: TELEGRAM_ALLOWED_USERNAME tanımlı değil.")
        return 1

    if args.no_supervise:
        _run_bot_once(args.db, username)
        return 0
    return supervise(lambda: _run_bot_once(args.db, username))


def cmd_scheduler(args: argparse.Namespace) -> int:
    from .scheduler import Scheduler
    from .telegram import TelegramClient

    client = TelegramClient()
    scheduler = Scheduler(
        history_callback=lambda: service.archive_and_notify(args.db, client),
        xg_sync_callback=lambda: service.auto_xg_sync(args.db),
        model_refresh_callback=lambda: service.auto_model_refresh(args.db),
        model_status_callback=lambda: service.push_model_status(args.db, client),
        push_callback=lambda: service.push_daily(args.db, client),
        result_callback=lambda: service.auto_results(args.db, client),
        context_callback=lambda: service.capture_fotmob_context(args.db),
        liveness_callback=lambda: service.check_bot_liveness(args.db, client),
        notification_callback=lambda: service.deliver_pending_notifications(
            args.db, client
        ),
        interval=args.interval,
        db_path=args.db,
    )
    scheduler.run()
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
    by_kind = service.metrics_by_kind(args.db)
    goal_metrics = service.surprise_category_metrics(args.db)
    print("=== Performans metrikleri (düz 1 birim bahis) ===")
    print(f"  Sonuçlanan kupon : {m['coupons_played']} (toplam {m['coupons_total']})")
    print(f"  Tutan            : {m['won']}")
    print(f"  İsabet oranı     : %{m['hit_rate'] * 100:.1f}")
    print(f"  Ortalama oran    : {m['avg_odds']:.2f}")
    print(f"  Kâr/Zarar        : {m['profit']:+.2f} birim")
    print(f"  ROI              : %{m['roi'] * 100:.1f}")
    print("\n=== Bağımsız kanıt kapıları ===")
    for kind, item in by_kind.items():
        status = "GEÇTİ" if item["gate_passed"] else "BEKLİYOR"
        print(
            f"  {kind:12} {item['matches']:4}/{item['minimum_matches']} mac | "
            f"ROI %{item['roi']*100:+.1f} | "
            f"{probability.interval_text(item['roi_ci95'])} | {status}"
        )
    print("\n=== Yüksek gol kategorileri ===")
    for item in goal_metrics.values():
        print(
            f"  {'6+ Gol':10}: {item['candidates']} aday, "
            f"{item['wins']} tutan, isabet %{item['hit_rate']*100:.1f}, "
            f"ROI %{item['roi']*100:+.1f}"
        )
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


def cmd_history_auto(args: argparse.Namespace) -> int:
    report = service.auto_history_archive(args.db, force=args.force)
    if report["skipped"]:
        print(f"Gece arşivi atlandı: {report['reason']}")
        return 0
    print(
        f"Gece arşivi: {len(report['days'])} gün, "
        f"{report['rows']} maç kaydedildi"
    )
    for error in report["errors"]:
        print(f"  {error['date']}: {error['error']}")
    return 0 if not report["errors"] else 1


def cmd_model_status(args: argparse.Namespace) -> int:
    text = service.model_status_text(args.db)
    print(text)
    if args.notify:
        from .telegram import TelegramClient

        chat_id = service.push_model_status(
            args.db, TelegramClient(), force=True
        )
        print("Telegram bildirimi gönderildi." if chat_id else "Chat bulunamadı.")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from .model import backtest

    with Database(args.db) as db:
        history = db.load_historical_matches()
    report = backtest(
        history,
        test_days=args.test_days,
        test_end_date=args.test_end,
        use_xg=not args.no_xg,
        xg_covered_only=args.xg_covered_only,
        markets=set(args.market) if args.market else None,
        outcomes={
            {
                "under": "Alt 2.5",
                "over": "Üst 2.5",
                "home": "1",
                "draw": "0",
                "away": "2",
            }[item]
            for item in args.outcome
        } if args.outcome else None,
    )
    if report.get("error"):
        print("Backtest yapılamadı:", report["error"])
        return 1
    print("=== Kronolojik model backtest (rapor modu) ===")
    print(f"  Eğitim maçı     : {report['train_matches']}")
    print(
        f"  xG eğitim maçı  : {report['xg_train_matches']} "
        f"({'AÇIK' if report['xg_enabled'] else 'KAPALI'})"
    )
    print(f"  Test maçı       : {report['test_matches']}")
    if report["xg_covered_only"]:
        print("  Test kapsamı    : iki takımda da eğitimden ≥3 xG maçı")
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
            f"{values['wins']} kazanan, ROI %{values['roi'] * 100:.1f} "
            f"({probability.interval_text(values['roi_ci95'])})"
        )
    for outcome, values in report["outcome_breakdown"].items():
        if not values["bets"]:
            continue
        print(
            f"    {outcome:13}: {values['bets']} seçim, "
            f"ROI %{values['roi']*100:.1f} "
            f"({probability.interval_text(values['roi_ci95'])})"
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
        f"({probability.interval_text(report['roi_ci95'])}), "
        f"ort. oran {report['avg_odds']:.2f}, "
        f"ort. edge %{report['avg_edge']*100:.1f}"
    )
    print(
        "Kabul kapısı: "
        + ("GEÇTİ" if report["gate_passed"] else "KALDI (canlıya alınamaz)")
    )
    return 0


def cmd_calibration(args: argparse.Namespace) -> int:
    report = service.refresh_calibration(args.db)
    print("=== Kalibrasyon katmanı ===")
    print(
        f"Uydurma verisi: {report['fit_matches']} maç · "
        f"ölçüm verisi: {report['holdout_matches']} maç"
    )
    for market, item in report["markets"].items():
        values = item.get("holdout") or item["fit"]
        print(f"\n{market}: {values['samples']} seçim")
        print(
            f"  piyasa ağırlığı {values['market_weight']:.3f} · "
            f"model ağırlığı {values['model_weight']:+.3f}"
        )
        print(
            f"  log kaybı — piyasa {values['market_logloss']:.5f} · "
            f"model {values['model_logloss']:.5f} · "
            f"harman {values['pooled_logloss']:.5f}"
        )
        print(f"  modelin katkısı: {values['model_contribution']:+.5f}")
    print()
    print(service.calibration_text(args.db).splitlines()[0])
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
        main_min_odds=args.main_min_odds,
        alt_min_odds=args.alt_min_odds,
    )
    print("=== Günlük kupon motoru tarihsel replay ===")
    print(
        "Yöntem: kapanış oranı / kısmi pazar "
        "(1X2 + Alt/Üst 2.5; 10:00 anlık oranı değildir)"
    )
    print(
        f"Alt sınır: ana {report['main_min_odds']:.2f} / "
        f"alternatif {report['alt_min_odds']:.2f}"
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
    any_hit = report["daily_any_hit"]
    print(
        f"Gün bazında : {any_hit['days']} günün "
        f"{any_hit['days_with_a_win']}'inde en az bir kupon tuttu "
        f"(%{any_hit['rate']*100:.1f})"
    )
    return 0


def cmd_fotmob_context(args: argparse.Namespace) -> int:
    report = service.capture_fotmob_context(
        args.db,
        detail_window_hours=args.detail_window,
        detail_limit=args.detail_limit,
        force=True,
    )
    print("=== FotMob bağlamsal veri (RAPOR MODU) ===")
    print(
        f"iddaa {report['events']} maç, FotMob {report['fixtures']} maç, "
        f"eşleşen {report['matched']} (%{report['match_rate']*100:.1f})"
    )
    print(
        f"Detay {report['details']}, doğrulanmış kadro {report['lineups']} "
        f"(maç önü {report['prematch_lineups']}), "
        f"xG bulunan {report['xg']}"
    )
    print("Canlı kupon etkisi: KAPALI")
    for error in report["errors"]:
        print(f"  {error['scope']}: {error['error']}")
    return 0


def cmd_weather(args: argparse.Namespace) -> int:
    report = service.collect_weather(
        args.db, venue_limit=args.venue_limit, horizon_days=args.days
    )
    print("=== Maç günü hava verisi (RAPOR MODU) ===")
    print(
        f"Sahasında oynayan {report['playing']} takım | "
        f"bilinen stadyum {report['already_known']}, "
        f"yeni çözülen {report['resolved']}, yazılan gün {report['days']}"
    )
    print("Canlı kupon etkisi: kalibrasyon katmanı karar verir")
    for error in report["errors"][:10]:
        print(f"  {error['scope']}: {error['error']}")
    return 0


def cmd_fotmob_lineup_backfill(args: argparse.Namespace) -> int:
    report = service.backfill_prior_lineups(
        args.db, per_team=args.per_team, team_limit=args.team_limit
    )
    print("=== FotMob önceki kadro backfill ===")
    print(
        f"Takım {report['teams']}, önceki maç {report['fixtures']}, "
        f"kadro {report['lineups']}, xG {report['xg']}"
    )
    for error in report["errors"]:
        print(f"  {error['scope']}: {error['error']}")
    return 0


def cmd_lineup_risk(args: argparse.Namespace) -> int:
    report = service.lineup_risk_report(
        args.db, high_rotation_changes=args.high_rotation
    )
    print("=== Kadro rotasyon raporu (SEÇİM ETKİSİ KAPALI) ===")
    print(
        f"Güncel kadro {report['matches']}, önceki kadroyla karşılaştırılabilen "
        f"{report['comparable']}, yüksek rotasyon {len(report['high_rotation'])}"
    )
    for item in report["high_rotation"]:
        when = datetime.fromtimestamp(
            item["start_ts"], tz=config.TIMEZONE
        ).strftime("%H:%M")
        print(
            f"  {when} {item['home']} - {item['away']}: "
            f"ev {item['home_changes']}, dep {item['away_changes']} değişiklik"
        )
    return 0


def cmd_xg_backfill(args: argparse.Namespace) -> int:
    from .understat import UNDERSTAT_LEAGUES

    leagues = tuple(args.league) if args.league else UNDERSTAT_LEAGUES
    report = service.backfill_understat_xg(
        args.db,
        seasons=args.season,
        leagues=leagues,
    )
    print("=== Understat tarihsel xG backfill ===")
    print(
        f"Çekilen {report['fetched']}, kaydedilen {report['saved']}, "
        f"geçmişle eşleşen {report['linked']} "
        f"(%{report['coverage']*100:.1f})"
    )
    for error in report["errors"]:
        print(f"  {error['scope']}: {error['error']}")
    return 0 if not report["errors"] else 1


def cmd_xg_auto(args: argparse.Namespace) -> int:
    report = service.auto_xg_sync(args.db, force=args.force)
    if report["skipped"]:
        print(f"xG senkronizasyonu atlandı: {report['reason']}")
        return 0
    print(
        f"xG senkronizasyonu: {report['fetched']} kaynak maç, "
        f"{report['linked']} tarihsel eşleşme"
    )
    for error in report["errors"]:
        print(f"  {error['scope']}: {error['error']}")
    return 0 if not report["errors"] else 1


def cmd_model_refresh(args: argparse.Namespace) -> int:
    report = service.auto_model_refresh(args.db, force=args.force)
    if report["skipped"]:
        print(f"Model eğitimi atlandı: {report['reason']}")
        return 0
    for run in report["runs"]:
        print(
            f"Model eğitildi: {run['model_version']} | "
            f"{run['history_matches']} maç | {run['xg_matches']} xG maçı | "
            f"{run['feature_rows']} feature"
        )
    return 0


def cmd_shadow_predict(args: argparse.Namespace) -> int:
    report = service.capture_shadow_predictions(args.db)
    print(
        f"Gölge tahmin: {report['eligible']} uygun, "
        f"{report['saved']} yeni kayıt | {report['by_model']}"
    )
    print("Canlı kupon etkisi: KAPALI")
    return 0


def cmd_shadow_metrics(args: argparse.Namespace) -> int:
    report = service.shadow_model_metrics(args.db, args.model_version)
    print(f"=== Gölge model: {report['model_version']} ===")
    print(
        f"Tahmin {report['predictions']}, kazanan {report['wins']}, "
        f"ROI %{report['roi']*100:.1f}, "
        f"{probability.interval_text(report['roi_ci95'])}"
    )
    print(
        f"Brier {report['brier'] if report['brier'] is not None else '—'} | "
        f"Kapı {'GEÇTİ' if report['gate_passed'] else 'BEKLİYOR'}"
    )
    return 0


def cmd_walk_forward(args: argparse.Namespace) -> int:
    report = service.build_walk_forward_archive(
        args.db,
        start_date=args.start,
        end_date=args.end,
        model_version=args.model_version,
    )
    print("=== Walk-forward tahmin arşivi ===")
    print(
        f"Dönem {report['start_date']}..{report['end_date']} | "
        f"{report['trained_days']} günlük model | "
        f"{report['eligible_predictions']} uygun tahmin | "
        f"{report['saved']} yeni kayıt"
    )
    return 0


def cmd_walk_forward_metrics(args: argparse.Namespace) -> int:
    report = service.walk_forward_metrics(
        args.db,
        model_version=args.model_version,
        min_edge=args.min_edge,
    )
    print(f"=== Walk-forward: {report['model_version']} ===")
    print(
        f"Ham {report['all_predictions']}, edge≥%{report['min_edge']*100:.1f} "
        f"{report['qualified_predictions']}, kazanan {report['wins']}"
    )
    print(
        f"İsabet %{report['hit_rate']*100:.1f}, "
        f"ort. oran {report['avg_odds']:.2f}, ROI %{report['roi']*100:.1f}, "
        f"{probability.interval_text(report['roi_ci95'])}"
    )
    if report["model_brier"] is not None:
        print(
            f"Brier model {report['model_brier']:.4f} | "
            f"piyasa {report['market_brier']:.4f}"
        )
    for outcome, item in report["outcomes"].items():
        print(
            f"  {outcome}: {item['predictions']} tahmin, "
            f"{item['wins']} kazanan, ROI %{item['roi']*100:.1f}"
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
            odds = mk.odds
            if len(odds) != len(mk.selections) or not all(
                odd is not None and odd > 1.0 for odd in odds
            ):
                continue
            fps = probability.fair_probs(odds)
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
    p_coupon.add_argument(
        "--rebuild",
        action="store_true",
        help="Replace today's coupons if none of their matches have started",
    )
    p_coupon.set_defaults(func=cmd_coupon)

    p_surprise = sub.add_parser("surprise", help="Build the surprise-lab report")
    p_surprise.set_defaults(func=cmd_surprise)

    p_bot = sub.add_parser("bot", help="Run the single-user Telegram bot")
    p_bot.add_argument(
        "--no-supervise",
        action="store_true",
        help="Exit on failure instead of restarting the bot automatically",
    )
    p_bot.set_defaults(func=cmd_bot)

    p_scheduler = sub.add_parser(
        "scheduler", help="Run scheduled archive, notification and capture work"
    )
    p_scheduler.add_argument(
        "--interval",
        type=float,
        default=config.SCHEDULER_INTERVAL_SECONDS,
        help="Seconds between callback cycles",
    )
    p_scheduler.set_defaults(func=cmd_scheduler)

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

    p_history_auto = sub.add_parser(
        "history-auto", help="Archive recent complete days with retries"
    )
    p_history_auto.add_argument("--force", action="store_true")
    p_history_auto.set_defaults(func=cmd_history_auto)

    p_model_status = sub.add_parser(
        "model-status", help="Print or send the 09:45 model health report"
    )
    p_model_status.add_argument("--notify", action="store_true")
    p_model_status.set_defaults(func=cmd_model_status)

    p_backtest = sub.add_parser(
        "backtest", help="Run chronological contextual-model backtest"
    )
    p_backtest.add_argument("--test-days", type=int, default=14)
    p_backtest.add_argument(
        "--test-end", help="Inclusive holdout end date (YYYY-MM-DD)"
    )
    p_backtest.add_argument("--no-xg", action="store_true")
    p_backtest.add_argument("--xg-covered-only", action="store_true")
    p_backtest.add_argument(
        "--market", action="append", choices=("1X2", "OU25")
    )
    p_backtest.add_argument(
        "--outcome",
        action="append",
        choices=("under", "over", "home", "draw", "away"),
    )
    p_backtest.set_defaults(func=cmd_backtest)

    p_weather = sub.add_parser(
        "weather",
        help="Bugünün takımları için stadyum ve maç günü hava verisi topla",
    )
    p_weather.add_argument("--venue-limit", type=int, default=60)
    p_weather.add_argument("--days", type=int, default=3)
    p_weather.set_defaults(func=cmd_weather)

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
    p_replay.add_argument(
        "--main-min-odds",
        type=float,
        help="Override the minimum payout of the main coupon",
    )
    p_replay.add_argument(
        "--alt-min-odds",
        type=float,
        help="Override the minimum payout of the alternative coupon",
    )
    p_replay.set_defaults(func=cmd_coupon_replay)

    p_fotmob = sub.add_parser(
        "fotmob-context", help="Capture report-only xG and lineup context"
    )
    p_fotmob.add_argument("--detail-window", type=float, default=2.0)
    p_fotmob.add_argument("--detail-limit", type=int, default=30)
    p_fotmob.set_defaults(func=cmd_fotmob_context)

    p_lineups = sub.add_parser(
        "fotmob-lineup-backfill",
        help="Backfill prior official lineups for current matched teams",
    )
    p_lineups.add_argument("--per-team", type=int, default=1)
    p_lineups.add_argument("--team-limit", type=int, default=30)
    p_lineups.set_defaults(func=cmd_fotmob_lineup_backfill)

    p_risk = sub.add_parser(
        "lineup-risk", help="Report high rotation from confirmed lineups"
    )
    p_risk.add_argument("--high-rotation", type=int, default=5)
    p_risk.set_defaults(func=cmd_lineup_risk)

    p_xg = sub.add_parser(
        "xg-backfill", help="Fetch and link bulk Understat league xG"
    )
    p_xg.add_argument("--season", type=int, action="append", required=True)
    p_xg.add_argument("--league", action="append")
    p_xg.set_defaults(func=cmd_xg_backfill)

    p_xg_auto = sub.add_parser(
        "xg-auto", help="Sync current xG after nightly archive"
    )
    p_xg_auto.add_argument("--force", action="store_true")
    p_xg_auto.set_defaults(func=cmd_xg_auto)

    p_model_refresh = sub.add_parser(
        "model-refresh", help="Train and record the daily model snapshot"
    )
    p_model_refresh.add_argument("--force", action="store_true")
    p_model_refresh.set_defaults(func=cmd_model_refresh)

    p_calibration = sub.add_parser(
        "calibration",
        help="Refit the market/model calibration layer and report its verdict",
    )
    p_calibration.set_defaults(func=cmd_calibration)

    p_shadow = sub.add_parser(
        "shadow-predict", help="Capture report-only xG O/U predictions"
    )
    p_shadow.set_defaults(func=cmd_shadow_predict)

    p_shadow_metrics = sub.add_parser(
        "shadow-metrics", help="Report settled xG shadow predictions"
    )
    p_shadow_metrics.add_argument(
        "--model-version", default=config.MODEL_SHADOW_VERSION
    )
    p_shadow_metrics.set_defaults(func=cmd_shadow_metrics)

    p_walk = sub.add_parser(
        "walk-forward", help="Build leakage-safe historical prediction archive"
    )
    p_walk.add_argument("--start", required=True)
    p_walk.add_argument("--end", required=True)
    p_walk.add_argument(
        "--model-version", default=config.MODEL_WALK_FORWARD_VERSION
    )
    p_walk.set_defaults(func=cmd_walk_forward)

    p_walk_metrics = sub.add_parser(
        "walk-forward-metrics", help="Report walk-forward ROI and calibration"
    )
    p_walk_metrics.add_argument(
        "--model-version", default=config.MODEL_WALK_FORWARD_VERSION
    )
    p_walk_metrics.add_argument(
        "--min-edge", type=float, default=config.MODEL_MIN_EDGE
    )
    p_walk_metrics.set_defaults(func=cmd_walk_forward_metrics)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
