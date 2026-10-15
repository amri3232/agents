import pytest

from conftest import make_market
from models import BUY, DOWN, SELL, UP
from state import Journal, Portfolio, utc_day


def pf_with(cfg, tmp_path, journal=True):
    return Portfolio(cfg, tmp_path / "state.json", Journal(tmp_path / "trades.csv") if journal else None)


def test_buy_accumulates_cost_with_fee(cfg, tmp_path):
    pf, m = pf_with(cfg, tmp_path), make_market()
    pf.apply_fill(m, UP, BUY, 10, 0.50, fee=0.09)
    pf.apply_fill(m, UP, BUY, 10, 0.60, fee=0.11)
    pos = pf.get(m.slug, UP)
    assert pos.shares == 20 and pos.cost == pytest.approx(5.0 + 6.0 + 0.20) and pos.fees == pytest.approx(0.20)
    assert pf.window_cost(m.slug) == pytest.approx(11.2) and pf.shares(m.slug, UP) == 20


def test_win_and_loss_settlement(cfg, tmp_path):
    pf, m = pf_with(cfg, tmp_path), make_market()
    pf.apply_fill(m, UP, BUY, 10, 0.50, fee=0.1)
    pnl = pf.settle(pf.get(m.slug, UP), 1.0)
    assert pnl == pytest.approx(10 - 5.1) and pf.wins == 1 and pf.consecutive_losses == 0
    m2 = make_market(1_700_000_400)
    pf.apply_fill(m2, DOWN, BUY, 10, 0.40)
    assert pf.settle(pf.get(m2.slug, DOWN), 0.0) == pytest.approx(-4.0)
    assert pf.losses == 1 and pf.consecutive_losses == 1
    assert pf.realized_pnl == pytest.approx(4.9 - 4.0) and pf.drawdown == pytest.approx(4.0)
    assert not pf.pending_settlement()


def test_split_resolution_pays_half(cfg, tmp_path):
    pf, m = pf_with(cfg, tmp_path), make_market()
    pf.apply_fill(m, UP, BUY, 10, 0.40)
    assert pf.settle(pf.get(m.slug, UP), 0.5) == pytest.approx(1.0)


def test_partial_sell_realizes_pnl_and_keeps_cost_basis(cfg, tmp_path):
    pf, m = pf_with(cfg, tmp_path), make_market()
    pf.apply_fill(m, UP, BUY, 10, 0.50)
    pnl = pf.apply_fill(m, UP, SELL, 4, 0.70, fee=0.05)
    assert pnl == pytest.approx(4 * 0.70 - 0.05 - 4 * 0.50)
    pos = pf.get(m.slug, UP)
    assert pos.shares == 6 and pos.cost == pytest.approx(3.0) and pf.realized_pnl == pytest.approx(pnl)
    pf.apply_fill(m, UP, SELL, 6, 0.70)
    assert pf.shares(m.slug, UP) == 0 and not pf.pending_settlement()


def test_persistence_roundtrip_including_halt(cfg, tmp_path):
    pf, m = pf_with(cfg, tmp_path), make_market()
    pf.apply_fill(m, UP, BUY, 10, 0.50)
    pf._book_pnl(-3.0)
    pf.halt("Test-Halt")
    fresh = pf_with(cfg, tmp_path)
    assert fresh.load()
    assert fresh.halted and fresh.halt_reason == "Test-Halt"
    assert fresh.realized_pnl == pytest.approx(-3.0)
    assert fresh.get(m.slug, UP).shares == 10
    fresh.reset_halt()
    again = pf_with(cfg, tmp_path)
    again.load()
    assert not again.halted


def test_corrupt_state_file_does_not_crash(cfg, tmp_path):
    (tmp_path / "state.json").write_text("{kaputt")
    pf = pf_with(cfg, tmp_path)
    assert pf.load() is False and pf.realized_pnl == 0


def test_journal_written(cfg, tmp_path):
    pf, m = pf_with(cfg, tmp_path), make_market()
    pf.apply_fill(m, UP, BUY, 10, 0.50, fair=0.7, edge=0.1)
    pf.settle(pf.get(m.slug, UP), 1.0)
    lines = (tmp_path / "trades.csv").read_text().strip().splitlines()
    assert lines[0].startswith("ts,mode,event") and len(lines) == 3
    assert ",FILL," in lines[1] and ",SETTLE," in lines[2]


def test_daily_pnl_rolls_over(cfg, tmp_path):
    pf = pf_with(cfg, tmp_path, journal=False)
    pf._book_pnl(-5)
    assert pf.day_pnl == -5
    pf.day = "1999-01-01"
    pf.roll_day()
    assert pf.day == utc_day() and pf.day_pnl == 0


def test_prune_removes_old_finished_and_fully_sold_positions(cfg, tmp_path):
    pf, m = pf_with(cfg, tmp_path, journal=False), make_market()
    pf.apply_fill(m, UP, BUY, 10, 0.5)
    pf.apply_fill(m, UP, SELL, 10, 0.6)                       # komplett verkauft -> nie "abzurechnen"
    m2 = make_market(1_700_000_400)
    pf.apply_fill(m2, DOWN, BUY, 10, 0.5)                     # offen
    pf.prune(now=m.end_ts + 10)                               # noch nicht alt genug
    assert len(pf.positions) == 2
    pf.prune(now=m2.end_ts + 7 * 3600)
    assert list(pf.positions) == [f"{m2.slug}:DOWN"]          # offene Position bleibt immer erhalten
