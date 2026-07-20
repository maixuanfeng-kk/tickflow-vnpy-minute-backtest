from datetime import datetime, timedelta

from vnpy.trader.constant import Direction, Exchange, Interval, Offset
from vnpy.trader.object import BarData, TradeData

from app.vnpy_backtest.engine import LocalNextBarOpenEngine
from app.vnpy_backtest.minute_double_ma import MinuteDoubleMaVolumeStrategy
from app.vnpy_backtest.trades import summarize_trades


def _bars() -> list[BarData]:
    start = datetime(2026, 1, 5, 9, 30)
    return [BarData(
        gateway_name="TEST",
        symbol="600000",
        exchange=Exchange.SSE,
        datetime=start + timedelta(minutes=i),
        interval=Interval.MINUTE,
        volume=100_000,
        turnover=1_000_000,
        open_price=12.0 if i >= 20 else 10.0,
        high_price=12.0 if i >= 20 else 10.0,
        low_price=12.0 if i >= 20 else 10.0,
        close_price=12.0 if i >= 20 else 10.0,
    ) for i in range(22)]


def test_vnpy_engine_replays_injected_local_bars() -> None:
    bars = _bars()
    engine = LocalNextBarOpenEngine(max_volume_ratio=None)
    engine.set_parameters(
        vt_symbol="600000.SSE",
        interval=Interval.MINUTE,
        start=bars[0].datetime,
        end=bars[-1].datetime,
        rate=0,
        slippage=0,
        size=1,
        pricetick=0.01,
        capital=100_000,
    )
    engine.add_strategy(MinuteDoubleMaVolumeStrategy, {
        "amount_multiple": 0.1,
        "initial_cash": 100_000,
        "max_volume_ratio": None,
    })
    engine.history_data = bars
    engine.run_backtesting()

    summary = summarize_trades(
        list(engine.trades.values()), bars, 100_000, 0.0, 0.0, 0.0, 0.0,
    )
    assert len(engine.trades) == 1
    assert summary["open_position"] > 0


def test_vnpy_engine_sizes_buy_at_next_bar_open_from_available_cash() -> None:
    bars = _bars()
    engine = LocalNextBarOpenEngine(max_volume_ratio=None)
    engine.set_parameters(
        vt_symbol="600000.SSE",
        interval=Interval.MINUTE,
        start=bars[0].datetime,
        end=bars[-1].datetime,
        rate=0,
        slippage=0,
        size=1,
        pricetick=0.01,
        capital=100_000,
    )
    engine.add_strategy(MinuteDoubleMaVolumeStrategy, {
        "initial_cash": 89_974.079921012,
        "cash_reserve_ratio": 0.03,
        "amount_multiple": 0.1,
        "max_volume_ratio": None,
    })
    bars[-1].open_price = 10.0
    engine.history_data = bars
    engine.run_backtesting()

    assert list(engine.trades.values())[0].volume == 8700


def test_trade_summary_allocates_partial_sell_against_partial_buy_cost() -> None:
    bars = _bars()
    trades = [
        TradeData(
            gateway_name="TEST", symbol="600000", exchange=Exchange.SSE,
            orderid="1", tradeid="1", direction=Direction.LONG, offset=Offset.OPEN,
            price=10.0, volume=700, datetime=bars[0].datetime,
        ),
        TradeData(
            gateway_name="TEST", symbol="600000", exchange=Exchange.SSE,
            orderid="2", tradeid="2", direction=Direction.SHORT, offset=Offset.CLOSE,
            price=9.9, volume=100, datetime=bars[1].datetime,
        ),
    ]

    summary = summarize_trades(trades, bars, 100_000, 0.0, 0.0, 0.0, 0.0)

    assert summary["trades"][0]["pnl"] == -10.0
