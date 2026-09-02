"""Score the goal model against the price it would be betting into.

Refits the model once per match day on what was known that morning, predicts
over/under 2.5 for that day's matches, and scores model and price on the same
matches with the same Brier. Nothing here looks forward.

Two things it exists to prevent.

The archive built by ``otomasyon.cli walk-forward`` only covers matches with
xG, and xG comes from Understat, which covers five leagues. The model was
therefore being judged on 1,891 matches in the most sharply priced leagues in
the world - 2.6% of the 72,578 the bulletin actually offers - and read as if
that were its record. ``--all-leagues`` drops the xG requirement and runs the
goal-only model over everything.

And a search over a few hundred noisy days will always find constants that
suit them, so ``--sweep`` chooses on days before ``--split`` and reports on
the days after. A number that has been fitted cannot also be the evidence.

    python3 scripts/model_vs_price.py --all-leagues
    python3 scripts/model_vs_price.py --sweep --split 2026-01-01 --workers 3
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import math
import statistics
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

from otomasyon import config, probability
from otomasyon.eligibility import is_event_eligible
from otomasyon.model import GoalModel
from otomasyon.storage import Database

_HISTORY: list[dict] = []
_TARGETS: dict[str, list[dict]] = {}
_USE_XG = True


def _load(db_path: str, use_xg: bool):
    with Database(db_path) as db:
        history = db.load_historical_matches()
    targets: dict[str, list[dict]] = {}
    for row in history:
        if use_xg and (row.get("xg_home") is None or row.get("xg_away") is None):
            continue
        if not (row.get("odds_under25") and row.get("odds_over25")):
            continue
        if row.get("ft_home") is None or row.get("ft_away") is None:
            continue
        if not is_event_eligible(
            row.get("competition") or "", row["home"], row["away"]
        ):
            continue
        targets.setdefault(row["match_date"], []).append(row)
    return history, targets


def _init(db_path: str, use_xg: bool) -> None:
    global _HISTORY, _TARGETS, _USE_XG
    _USE_XG = use_xg
    _HISTORY, _TARGETS = _load(db_path, use_xg)


def _cutoff(day: str) -> int:
    return int(
        dt.datetime.combine(
            dt.date.fromisoformat(day),
            dt.datetime.min.time(),
            tzinfo=config.TIMEZONE,
        ).timestamp()
    )


def _run(params: dict) -> list[dict]:
    for key, value in params.items():
        setattr(config, key, value)
    out: list[dict] = []
    for day in sorted(_TARGETS):
        model = GoalModel(_HISTORY, _cutoff(day), use_xg=_USE_XG)
        for row in _TARGETS[day]:
            pred = model.predict(row["home"], row["away"], row.get("competition"))
            if _USE_XG and (
                pred.xg_samples_home < 3 or pred.xg_samples_away < 3
            ):
                continue
            odds = [row["odds_under25"], row["odds_over25"]]
            fair = probability.fair_probs(odds)
            total = row["ft_home"] + row["ft_away"]
            # The archive's own rule: back the side the model likes most.
            if pred.probs["Alt 2.5"] - fair[0] >= pred.probs["Üst 2.5"] - fair[1]:
                p, m, odd, won = (
                    pred.probs["Alt 2.5"], fair[0], odds[0], total < 2.5
                )
            else:
                p, m, odd, won = (
                    pred.probs["Üst 2.5"], fair[1], odds[1], total > 2.5
                )
            out.append(
                {
                    "day": day,
                    "competition": row.get("competition") or "?",
                    "p": p,
                    "market": m,
                    "odd": odd,
                    "won": 1 if won else 0,
                    "edge": p - m,
                    "confidence": pred.confidence,
                    "margin": (1 / odds[0] + 1 / odds[1]) - 1.0,
                }
            )
    return out


def score(rows: list[dict]) -> dict:
    """Model and price on the same bets, with the gap's own error bar."""
    if not rows:
        return {"n": 0}
    model = statistics.mean((r["p"] - r["won"]) ** 2 for r in rows)
    market = statistics.mean((r["market"] - r["won"]) ** 2 for r in rows)
    paired = [
        (r["p"] - r["won"]) ** 2 - (r["market"] - r["won"]) ** 2 for r in rows
    ]
    se = statistics.stdev(paired) / math.sqrt(len(paired)) if len(rows) > 1 else 0.0
    return {
        "n": len(rows),
        "model": model,
        "market": market,
        "gap": model - market,
        "gap_ci": 1.96 * se,
        "roi": statistics.mean((r["odd"] - 1) if r["won"] else -1.0 for r in rows),
    }


def _line(label: str, rows: list[dict], floor: int = 200) -> None:
    if len(rows) < floor:
        return
    s = score(rows)
    who = "MODEL" if s["gap"] < 0 else "piyasa"
    print(
        f"  {label:<30}{s['n']:>7} | model {s['model']:.4f} piyasa {s['market']:.4f}"
        f" | açık {s['gap']:>+7.4f} ±{s['gap_ci']:.4f} ({who} önde)"
        f" | ROI %{s['roi'] * 100:>+6.1f}"
    )


def _report(rows: list[dict]) -> None:
    print(f"\n{len(rows)} tahmin, {len({r['day'] for r in rows})} gün\n")
    print("TAMAMI")
    _line("hepsi", rows)
    print("\nMODEL PİYASADAN NE KADAR AYRIŞIRSA")
    for edge in (0.02, 0.04, 0.06, 0.08, 0.12):
        _line(f"edge >= %{edge * 100:.0f}", [r for r in rows if abs(r["edge"]) >= edge])
    print("\nPİYASA MARJINA GÖRE (yüksek marj = ince piyasa)")
    q = sorted(r["margin"] for r in rows)
    lo, hi = q[len(q) // 4], q[3 * len(q) // 4]
    _line("en kalın çeyrek", [r for r in rows if r["margin"] <= lo])
    _line("en ince çeyrek", [r for r in rows if r["margin"] > hi])
    print("\nLİGE GÖRE (en çok tahmin alan sekiz)")
    by = defaultdict(list)
    for r in rows:
        by[r["competition"]].append(r)
    for name, group in sorted(by.items(), key=lambda kv: -len(kv[1]))[:8]:
        _line(name[:29], group)


def _job(item):
    name, params = item
    return name, params, _run(params)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=config.DB_PATH)
    parser.add_argument(
        "--all-leagues",
        action="store_true",
        help="Drop the xG requirement and run the goal-only model everywhere",
    )
    parser.add_argument("--sweep", action="store_true", help="Search the constants")
    parser.add_argument("--split", default="2026-01-01")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    use_xg = not args.all_leagues
    if not args.sweep:
        _init(args.db, use_xg)
        kapsam = "tüm ligler, gol modeli" if args.all_leagues else "xG'li maçlar"
        print(f"Kapsam: {kapsam}")
        _report(_run({}))
        return 0

    grid = {
        "MODEL_PRIOR_MATCHES": [5.0, 12.0, 20.0, 35.0, 60.0],
        "MODEL_HALF_LIFE_DAYS": [90.0, 180.0, 240.0, 400.0],
        "MODEL_XG_BLEND": [0.0, 0.9, 1.0],
    }
    keys = list(grid)
    sets = [dict(zip(keys, v)) for v in itertools.product(*grid.values())]
    print(f"{len(sets)} parametre seti, {args.workers} işlemci\n")

    results = []
    with ProcessPoolExecutor(
        max_workers=args.workers, initializer=_init, initargs=(args.db, use_xg)
    ) as pool:
        for i, (_, params, rows) in enumerate(
            pool.map(_job, list(enumerate(sets))), 1
        ):
            chosen = score([r for r in rows if r["day"] < args.split])
            judged = score([r for r in rows if r["day"] >= args.split])
            results.append((params, chosen, judged))
            print(
                f"[{i}/{len(sets)}] prior={params['MODEL_PRIOR_MATCHES']:<5}"
                f" yarı_ömür={params['MODEL_HALF_LIFE_DAYS']:<6}"
                f" xg={params['MODEL_XG_BLEND']:<4}"
                f" | seçim açığı {chosen.get('gap', 0):+.4f}"
                f" | hüküm açığı {judged.get('gap', 0):+.4f}",
                flush=True,
            )

    results.sort(key=lambda r: r[1].get("gap", 1.0))
    print("\n=== SEÇİM DÖNEMİNE GÖRE EN İYİ BEŞ ===")
    for params, chosen, judged in results[:5]:
        print(
            f"  prior={params['MODEL_PRIOR_MATCHES']:<5}"
            f" yarı_ömür={params['MODEL_HALF_LIFE_DAYS']:<6}"
            f" xg={params['MODEL_XG_BLEND']:<4}"
            f" | seçim {chosen['gap']:+.4f} | hüküm {judged.get('gap', 0):+.4f}"
        )
    params, _, judged = results[0]
    print("\n=== SEÇİLEN SETİN HÜKÜM DÖNEMİ ===")
    print(f"  {params}")
    print(
        f"  {judged['n']} tahmin | model {judged['model']:.4f}"
        f" piyasa {judged['market']:.4f} | açık {judged['gap']:+.4f}"
        f" ±{judged['gap_ci']:.4f} | ROI %{judged['roi'] * 100:+.1f}"
    )
    if judged["gap"] + judged["gap_ci"] < 0:
        print("  -> Hüküm dönemi piyasanın yenildiğini söylüyor.")
    elif judged["gap"] < 0:
        print("  -> Önde ama fark güven aralığı içinde: kanıt yetersiz.")
    else:
        print("  -> Piyasa hâlâ önde.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
