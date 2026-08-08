from otomasyon.formatting import format_settlement
from otomasyon.settlement import CouponSettlement


def test_winning_notification_distinguishes_return_from_net_profit():
    coupon = {
        "kind": "daily_alt",
        "for_date": "2026-08-08",
        "legs": [],
    }
    settlement = CouponSettlement(
        status="won",
        effective_odds=1.27 * 1.58,
        profit=1.27 * 1.58 - 1.0,
    )
    text = format_settlement(coupon, settlement)
    assert "Kupon oranı: 2.01" in text
    assert "Toplam geri dönüş (1 birim): 2.01" in text
    assert "Net kâr: +1.01 birim" in text


def test_losing_notification_shows_zero_return_and_lost_stake():
    coupon = {"kind": "daily_main", "for_date": "2026-08-08", "legs": []}
    settlement = CouponSettlement(status="lost", effective_odds=0.0, profit=-1.0)
    text = format_settlement(coupon, settlement)
    assert "Toplam geri dönüş (1 birim): 0.00" in text
    assert "Net kâr: -1.00 birim" in text
