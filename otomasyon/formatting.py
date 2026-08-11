"""Plain-text formatting of coupons and surprise reports for Telegram/CLI."""

from __future__ import annotations

from datetime import datetime

from . import config


def _hm(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=config.TIMEZONE).strftime("%H:%M")


def _dm(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=config.TIMEZONE).strftime("%d.%m %H:%M")


def format_coupon(title: str, coupon) -> str:
    if coupon is None:
        return f"{title}: uygun kupon bulunamadı."
    lines = [
        f"{title}  |  Toplam oran: {coupon.total_odds:.2f}  |  "
        f"Tutma olasılığı: %{coupon.combined_prob * 100:.1f}  |  "
        f"{len(coupon.legs)} maç  |  "
        f"kupon marjı %{coupon.cumulative_margin * 100:.1f}"
    ]
    for leg in coupon.legs:
        lines.append(
            f"  • [{_hm(leg.start_ts)}] {leg.home} - {leg.away}\n"
            f"      {leg.market_name}: {leg.outcome_name} @ {leg.odd}  "
            f"(adil %{leg.fair_prob * 100:.0f})"
        )
    return "\n".join(lines)


def format_daily(coupons: dict, for_date: str) -> str:
    if not coupons.get("main") and not coupons.get("alt"):
        return (
            f"Günün kuponu ({for_date}) henüz hazır değil.\n"
            f"{config.DAILY_MIN_TOTAL_ODDS:.2f}–{config.DAILY_MAX_TOTAL_ODDS:.2f} "
            "bandında uygun kurgu bulunamadı."
        )
    parts = [f"GÜNÜN DÜŞÜK RİSKLİ KUPONLARI ({for_date})", ""]
    parts.append(format_coupon("ANA KUPON", coupons.get("main")))
    parts.append("")
    parts.append(format_coupon("ALTERNATİF", coupons.get("alt")))
    parts.append("")
    parts.append(
        "Not: Piyasa tabanlı deneme kuponudur; bağlamsal ROI modeli henüz "
        "kabul testini geçmemiştir. Bilgilendirme amaçlıdır, otomatik oynama "
        "yapılmaz. Oranlar kupon anındaki değerlerdir."
    )
    return "\n".join(parts)


_KIND_LABELS = {
    "daily_main": "Ana Kupon",
    "daily_alt": "Alternatif Kupon",
    "surprise": "Sürpriz Kupon",
}
_STATUS_LABELS = {
    "won": "KAZANDI",
    "lost": "KAYBETTİ",
    "void": "İPTAL",
    "pending": "BEKLİYOR",
}
_LEG_LABELS = {"win": "tuttu", "lose": "tutmadı", "void": "iptal", "pending": "bekliyor"}


def format_settlement(coupon: dict, settlement) -> str:
    kind = _KIND_LABELS.get(coupon.get("kind"), coupon.get("kind", "Kupon"))
    status = _STATUS_LABELS.get(settlement.status, settlement.status)
    lines = [f"KUPON SONUCU — {kind} ({coupon.get('for_date','')})", f"Durum: {status}"]
    if settlement.status == "won":
        lines.append(
            f"Kupon oranı: {settlement.effective_odds:.2f}  |  "
            f"Toplam geri dönüş (1 birim): {settlement.effective_odds:.2f}"
        )
        lines.append(f"Net kâr: +{settlement.profit:.2f} birim")
    elif settlement.status == "lost":
        lines.append("Toplam geri dönüş (1 birim): 0.00")
        lines.append("Net kâr: -1.00 birim")
    elif settlement.status == "void":
        lines.append("Toplam geri dönüş (1 birim): 1.00")
        lines.append("Net kâr: 0.00 birim")
    lines.append("")
    legs = coupon.get("legs", [])
    for leg, leg_res in zip(legs, settlement.legs):
        teams = f"{leg.get('home','?')} - {leg.get('away','?')}"
        lines.append(
            f"  • {teams} | {leg.get('market_name')}: "
            f"{leg.get('outcome_name')} @ {leg.get('odd')} → "
            f"{_LEG_LABELS.get(leg_res.result, leg_res.result)}"
        )
    return "\n".join(lines)


_CATEGORY_TITLES = {
    "goals_6plus": "6+ Gol",
}


def format_surprise(report) -> str:
    if not report.has_candidates:
        return (
            "Yüksek gol laboratuvarı henüz hazır değil.\n"
            "Uygun aday maç bulunamadı (bülten dar olabilir)."
        )
    parts = ["6+ GOL LABORATUVARI (yüksek risk, sistem)", ""]
    for category, cands in report.by_category.items():
        if not cands:
            continue
        parts.append(_CATEGORY_TITLES.get(category, category) + ":")
        for c in cands:
            parts.append(
                f"  • [{_dm(c.start_ts)}] {c.match}  "
                f"({c.outcome_name} @ {c.odd}, adil %{c.fair_prob * 100:.0f})"
            )
        parts.append("")

    if report.system_set:
        parts.append(
            f"SİSTEM SETİ ({len(report.system_set)} maç, farklı karşılaşmalar):"
        )
        for c in report.system_set:
            selection = _CATEGORY_TITLES.get(c.category, c.outcome_name)
            parts.append(
                f"  • [{_dm(c.start_ts)}] {c.match} — {selection} @ {c.odd}"
            )
        parts.append("")
        parts.append("Sistem senaryoları (teorik minimum maliyet):")
        for s in report.scenarios:
            label = "tam kombine" if s.size == s.total else f"{s.size}/{s.total}"
            parts.append(
                f"  • {label}: {s.columns} kolon  ≈ {s.min_cost:.0f} TL "
                f"(birim {s.unit_stake:.0f} TL)"
            )
        parts.append("")
    parts.append("Not: Bilgilendirme amaçlıdır, otomatik oynama yapılmaz.")
    return "\n".join(parts)
