"""``python main.py --report``: Auswertung des Trade-Journals inkl. Kalibrierung des Modells."""
from __future__ import annotations

from pathlib import Path


def print_report(path: Path) -> None:
    import pandas as pd

    if not path.exists() or path.stat().st_size == 0:
        print(f"Noch kein Journal unter {path} - der Bot hat noch keine Trades gemacht.")
        return
    df = pd.read_csv(path)
    for col in ("shares", "price", "fee", "fair", "edge", "pnl", "won"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d")
    fills = df[df.event == "FILL"]
    settled = df[(df.event == "SETTLE") & (df.shares > 0)]
    realized_sells = fills[(fills.side == "SELL") & fills.pnl.notna()]

    print(f"Journal: {path}  ({len(fills)} Fills, {len(settled)} abgerechnete Positionen)")
    if settled.empty and realized_sells.empty:
        print("Noch keine abgerechneten Trades.")
        return
    total = settled.pnl.sum() + realized_sells.pnl.sum()
    print(f"\nGesamt-PnL (realisiert): {total:+.2f} USD | Gebühren (Fills): {fills.fee.sum():.2f} USD")
    if not settled.empty:
        wins = (settled.won == 1).sum()
        n = len(settled)
        print(f"Trefferquote: {wins}/{n} = {wins / n:.1%} | Ø PnL je Position: {settled.pnl.mean():+.3f} USD")
        valid = settled.dropna(subset=["fair"])
        valid = valid[valid.fair > 0]
        if not valid.empty:
            brier_model = ((valid.fair - valid.won) ** 2).mean()
            brier_market = ((valid.price - valid.won) ** 2).mean()
            print("\nKalibrierung (entscheidend, bevor du LIVE gehst):")
            print(f"  Ø Modell-Wahrscheinlichkeit beim Einstieg : {valid.fair.mean():.3f}")
            print(f"  Ø Einstiegspreis (Markt-implizit)         : {valid.price.mean():.3f}")
            print(f"  Tatsächliche Trefferquote                 : {(valid.won == 1).mean():.3f}")
            print(f"  Brier-Score Modell / Markt (kleiner=besser): {brier_model:.4f} / {brier_market:.4f}")
            if brier_model >= brier_market:
                print("  -> Das Modell ist NICHT besser kalibriert als der Marktpreis: kein belegter Edge.")
    print("\nPnL je Tag (UTC):")
    daily = pd.concat([settled[["date", "pnl"]], realized_sells[["date", "pnl"]]]).groupby("date").pnl.sum()
    for day, pnl in daily.items():
        print(f"  {day}: {pnl:+.2f} USD")
