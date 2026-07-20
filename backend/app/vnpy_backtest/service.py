"""vn.py minute CTA backtest over TickFlow's stored minute K data."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import uuid4

from vnpy.trader.constant import Interval

from app.vnpy_backtest.engine import LocalNextBarOpenEngine
from app.vnpy_backtest.local_data import bars_from_minute_frame
from app.vnpy_backtest.minute_double_ma import MinuteDoubleMaVolumeStrategy
from app.vnpy_backtest.trades import summarize_trades


@dataclass(frozen=True)
class VnpyMinuteBacktestConfig:
    symbol: str
    start: date
    end: date
    initial_capital: float = 1_000_000.0
    commission_pct: float = 0.0002
    stamp_tax_pct: float = 0.001
    slippage_bps: float = 5.0
    lot_size: int = 100
    max_volume_ratio: float | None = 0.10
    params: dict[str, Any] = field(default_factory=dict)


class VnpyMinuteBacktestService:
    """Run the initial CTA strategy without involving Matrix backtest code."""

    def __init__(self, repo) -> None:
        self.repo = repo

    def run(self, config: VnpyMinuteBacktestConfig) -> dict:
        if config.end < config.start:
            raise ValueError("end date must not precede start date")

        frame = self.repo.get_minute_range(
            [config.symbol], config.start, config.end, asset_type="stock"
        )
        bars = bars_from_minute_frame(config.symbol, frame) if not frame.is_empty() else []
        if not bars:
            raise ValueError("no minute bars in the selected range")

        engine = LocalNextBarOpenEngine(
            max_volume_ratio=config.max_volume_ratio,
            lot_size=config.lot_size,
        )
        engine.set_parameters(
            vt_symbol=f"{bars[0].symbol}.{bars[0].exchange.value}",
            interval=Interval.MINUTE,
            start=bars[0].datetime,
            end=bars[-1].datetime,
            rate=0,
            slippage=0,
            size=1,
            pricetick=0.01,
            capital=config.initial_capital,
        )
        settings = {
            "initial_cash": config.initial_capital,
            "default_lot_size": config.lot_size,
            "max_volume_ratio": config.max_volume_ratio,
            **config.params,
        }
        engine.add_strategy(MinuteDoubleMaVolumeStrategy, settings)
        engine.history_data = bars
        engine.run_backtesting()

        summary = summarize_trades(
            list(engine.trades.values()),
            bars,
            config.initial_capital,
            config.commission_pct,
            config.stamp_tax_pct,
            0.0,
            config.slippage_bps / 10_000,
        )
        total_return = summary["total_return"]
        stats = {
            "mode": "vnpy",
            "total_trade_count": len(engine.trades),
            "total_return": total_return,
            "end_balance": summary["end_balance"],
            "total_pnl": summary["total_pnl"],
            "max_drawdown": None,
            "sharpe": None,
        }
        trades = [
            {
                "symbol": config.symbol,
                "name": config.symbol,
                "entry_date": trade["entry_datetime"],
                "exit_date": trade["exit_datetime"],
                "entry_datetime": trade["entry_datetime"],
                "exit_datetime": trade["exit_datetime"],
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "pnl_pct": trade["return_pct"],
                "pnl_amount": trade["pnl"],
                "duration": 0,
                "duration_minutes": 0,
                "exit_reason": "vnpy_strategy",
                "shares": trade["volume"],
                "lots": trade["volume"] / config.lot_size,
                "position_pct": None,
                "entry_value": trade["entry_price"] * trade["volume"],
                "exit_value": trade["exit_price"] * trade["volume"],
            }
            for trade in summary["trades"]
        ]
        return {
            "run_id": uuid4().hex,
            "config": {
                "engine": "vnpy",
                "frequency": "1m",
                "strategy_id": "minute_double_ma_volume",
                "symbols": [config.symbol],
                "start": str(config.start),
                "end": str(config.end),
                "initial_capital": config.initial_capital,
                "params": settings,
            },
            "stats": stats,
            "equity_curve": [
                {"date": bars[0].datetime.isoformat(sep=" "), "value": config.initial_capital},
                {"date": bars[-1].datetime.isoformat(sep=" "), "value": summary["end_balance"]},
            ],
            "drawdown_curve": [],
            "benchmark_curve": [],
            "trades": trades,
            "per_symbol_stats": [],
            "strategy_info": {
                "id": "minute_double_ma_volume",
                "name": "分钟双均线放量（vn.py）",
                "source": "vnpy",
            },
        }
