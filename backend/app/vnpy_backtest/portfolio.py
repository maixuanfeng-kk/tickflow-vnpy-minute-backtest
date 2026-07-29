"""A deterministic, multi-symbol minute portfolio executor using vn.py bars."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from math import floor
from typing import Mapping, Sequence

from vnpy.trader.constant import Direction
from vnpy.trader.object import BarData

from app.backtest.opening_volume_shared import rank_opening_volume_candidates
from app.vnpy_backtest.market_rules import AShareTradingRule, rule_for_symbol
from app.vnpy_backtest.strategies.base import (
    DailyReference,
    OrderIntent,
    PortfolioContext,
    PortfolioMinuteStrategy,
    PortfolioPositionView,
)


@dataclass
class PortfolioPosition:
    symbol: str
    volume: int = 0
    average_cost: float = 0.0
    entry_date: date | None = None
    entry_datetime: datetime | None = None


@dataclass
class PendingPortfolioOrder:
    symbol: str
    direction: Direction
    due_at: datetime
    reason: str
    volume: int | None = None
    budget: float = 0.0
    signal_id: int | None = None


@dataclass(frozen=True)
class PortfolioFill:
    symbol: str
    direction: Direction
    datetime: datetime
    price: float
    volume: int
    reason: str
    commission: float
    stamp_tax: float
    slippage: float
    portfolio_equity_before: float | None = None
    entry_position_pct: float | None = None
    signal_id: int | None = None


@dataclass(frozen=True)
class PortfolioRejection:
    symbol: str
    datetime: datetime
    reason: str
    signal_id: int | None = None


@dataclass
class PortfolioSignal:
    id: int
    symbol: str
    direction: Direction
    datetime: datetime
    reason: str
    diagnostic: dict[str, object] = field(default_factory=dict)
    status: str = "triggered"
    due_at: datetime | None = None
    fill_datetime: datetime | None = None
    rejection_reason: str | None = None


@dataclass
class PortfolioRunResult:
    cash: float
    positions: dict[str, PortfolioPosition]
    fills: list[PortfolioFill]
    rejections: list[PortfolioRejection]
    equity_curve: list[dict]
    last_prices: dict[str, float]
    signals: list[PortfolioSignal]


class DailyContextBuilder:
    """Build prior completed-day references from local minute bars only."""

    def __init__(self) -> None:
        self._history: dict[str, list[dict]] = defaultdict(list)

    def references(self) -> dict[str, DailyReference]:
        result: dict[str, DailyReference] = {}
        for symbol, rows in self._history.items():
            if not rows:
                continue
            previous = rows[-1]
            result[symbol] = DailyReference(
                previous_open=previous["open"],
                previous_close=previous["close"],
                previous_high=previous["high"],
                previous_low=previous["low"],
                previous_volume=previous["volume"],
                closes=tuple(row["close"] for row in rows[-5:]),
                previous_cumulative_volumes=previous["cumulative_volumes"],
            )
        return result

    def add_day(self, bars: Mapping[str, Sequence[BarData]]) -> None:
        for symbol, symbol_bars in bars.items():
            if not symbol_bars:
                continue
            cumulative_volume = 0.0
            cumulative_volumes = {}
            for bar in sorted(symbol_bars, key=lambda item: item.datetime):
                cumulative_volume += float(bar.volume)
                cumulative_volumes[bar.datetime.time()] = cumulative_volume
            self._history[symbol].append(
                {
                    "open": float(symbol_bars[0].open_price),
                    "high": max(float(bar.high_price) for bar in symbol_bars),
                    "low": min(float(bar.low_price) for bar in symbol_bars),
                    "close": float(symbol_bars[-1].close_price),
                    "volume": sum(float(bar.volume) for bar in symbol_bars),
                    "cumulative_volumes": cumulative_volumes,
                }
            )


class MultiSymbolNextBarOpenEngine:
    """Portfolio-level next-bar-open execution for future vn.py stock-pool strategies."""

    def __init__(
        self,
        *,
        initial_cash: float,
        commission_rate: float,
        stamp_tax_rate: float,
        slippage_rate: float,
        min_commission: float = 5.0,
        max_volume_ratio: float | None = None,
        max_buy_volume_ratio: float | None = None,
        max_sell_volume_ratio: float | None = None,
        max_positions: int = 10,
        position_sizing: str = "equal",
        reserve_ratio: float = 0.03,
        candidate_sort: str = "volume_ratio",
        score_weights: Mapping[str, float] | None = None,
        score_min: float | None = None,
        score_max: float | None = None,
        watchlist_order: Sequence[str] | None = None,
        instrument_names: Mapping[str, str] | None = None,
        instrument_limit_pcts: Mapping[str, float] | None = None,
    ) -> None:
        self.initial_cash = float(initial_cash)
        self.cash = self.initial_cash
        self.commission_rate = float(commission_rate)
        self.stamp_tax_rate = float(stamp_tax_rate)
        self.slippage_rate = float(slippage_rate)
        self.min_commission = float(min_commission)
        self.max_buy_volume_ratio = max_buy_volume_ratio if max_buy_volume_ratio is not None else max_volume_ratio
        self.max_sell_volume_ratio = max_sell_volume_ratio if max_sell_volume_ratio is not None else max_volume_ratio
        self.max_positions = max(1, int(max_positions))
        if position_sizing not in {"equal", "score_weight"}:
            raise ValueError("position_sizing must be equal or score_weight")
        self.position_sizing = position_sizing
        self.reserve_ratio = max(float(reserve_ratio), 0.0)
        self.reserve_cash = self.initial_cash * self.reserve_ratio
        self._daily_equal_budget: float | None = None
        if candidate_sort not in {"score", "volume_ratio", "watchlist_order"}:
            raise ValueError("candidate_sort must be score, volume_ratio, or watchlist_order")
        self.candidate_sort = candidate_sort
        self.score_weights = dict(score_weights or {})
        self.score_min = score_min
        self.score_max = score_max
        self.watchlist_order = tuple(watchlist_order or ())
        self.instrument_names = dict(instrument_names or {})
        self.instrument_limit_pcts = dict(instrument_limit_pcts or {})
        self.positions: dict[str, PortfolioPosition] = {}
        self.pending: list[PendingPortfolioOrder] = []
        self.fills: list[PortfolioFill] = []
        self.rejections: list[PortfolioRejection] = []
        self.equity_curve: list[dict] = []
        self.last_prices: dict[str, float] = {}
        self.signals: list[PortfolioSignal] = []

    def run_day(
        self,
        bars_by_symbol: Mapping[str, Sequence[BarData]],
        strategy: PortfolioMinuteStrategy,
        daily_references: Mapping[str, DailyReference],
        *,
        allow_entries: bool = True,
    ) -> None:
        by_timestamp: dict[datetime, dict[str, BarData]] = defaultdict(dict)
        next_times: dict[tuple[str, datetime], datetime] = {}
        for symbol, bars in bars_by_symbol.items():
            ordered = sorted(bars, key=lambda item: item.datetime)
            for index, bar in enumerate(ordered):
                by_timestamp[bar.datetime][symbol] = bar
                if index + 1 < len(ordered):
                    next_times[(symbol, bar.datetime)] = ordered[index + 1].datetime

        for timestamp in sorted(by_timestamp):
            bars = by_timestamp[timestamp]
            self._fill_due(timestamp, bars, daily_references)
            self.last_prices.update({symbol: float(bar.close_price) for symbol, bar in bars.items() if bar.close_price > 0})
            self._refresh_daily_equal_budget(bars)
            context = PortfolioContext(
                timestamp=timestamp,
                cash=self.cash,
                reserved_cash=sum(order.budget for order in self.pending if order.direction == Direction.LONG),
                positions={
                    symbol: PortfolioPositionView(symbol, position.volume, position.average_cost, position.entry_date)
                    for symbol, position in self.positions.items() if position.volume > 0
                },
                daily_references=daily_references,
            )
            intents = list(strategy.on_minute(bars, context))
            if not allow_entries:
                intents = [intent for intent in intents if intent.direction != Direction.LONG]
            self._queue_intents(intents, timestamp, next_times, context, bars)
            self._record_equity(timestamp)

        # next-bar orders that reached the end of the day are explicitly invalid.
        for order in list(self.pending):
            self._reject(order.symbol, order.due_at, "no_next_bar", order.signal_id)
            self.pending.remove(order)

    def result(self) -> PortfolioRunResult:
        return PortfolioRunResult(
            cash=self.cash,
            positions=self.positions,
            fills=self.fills,
            rejections=self.rejections,
            equity_curve=self.equity_curve,
            last_prices=self.last_prices,
            signals=self.signals,
        )

    def _queue_intents(
        self,
        intents: Sequence[OrderIntent],
        timestamp: datetime,
        next_times: Mapping[tuple[str, datetime], datetime],
        context: PortfolioContext,
        bars: Mapping[str, BarData],
    ) -> None:
        recorded = [(intent, self._record_signal(intent, timestamp)) for intent in intents]
        sells = [(intent, signal_id) for intent, signal_id in recorded if intent.direction == Direction.SHORT]
        buys = [(intent, signal_id) for intent, signal_id in recorded if intent.direction == Direction.LONG]
        for intent, signal_id in sells:
            due_at = next_times.get((intent.symbol, timestamp))
            if due_at is None:
                self._reject(intent.symbol, timestamp, "no_next_bar", signal_id)
                continue
            position = self.positions.get(intent.symbol)
            if not position or position.volume <= 0:
                self._reject(intent.symbol, timestamp, "no_position", signal_id)
                continue
            if position.entry_date == timestamp.date():
                self._reject(intent.symbol, timestamp, "t_plus_one", signal_id)
                continue
            self.pending.append(PendingPortfolioOrder(intent.symbol, intent.direction, due_at, intent.reason, volume=intent.volume or position.volume, signal_id=signal_id))
            signal = self._signal(signal_id)
            if signal:
                signal.status = "queued"
                signal.due_at = due_at

        active_symbols = {
            symbol for symbol, position in self.positions.items() if position.volume > 0
        } | {
            order.symbol for order in self.pending if order.direction == Direction.LONG
        }
        occupied_slots = len(active_symbols)
        selected: list[tuple[OrderIntent, int, datetime]] = []
        ranked_buys = self._prioritize_buys(buys, timestamp)
        ranked_signal_ids = {signal_id for _, signal_id in ranked_buys}
        for intent, signal_id in buys:
            if signal_id not in ranked_signal_ids:
                self._reject(intent.symbol, timestamp, "score_out_of_range", signal_id)
        for intent, signal_id in ranked_buys:
            due_at = next_times.get((intent.symbol, timestamp))
            if due_at is None:
                self._reject(intent.symbol, timestamp, "no_next_bar", signal_id)
                continue
            if intent.symbol in active_symbols:
                self._reject(intent.symbol, timestamp, "already_held", signal_id)
                continue
            if len(active_symbols) >= self.max_positions:
                self._reject(intent.symbol, timestamp, "max_positions", signal_id)
                continue
            active_symbols.add(intent.symbol)
            selected.append((intent, signal_id, due_at))

        available = max(context.available_cash - self.reserve_cash, 0.0)
        remaining_slots = max(self.max_positions - occupied_slots, 0)
        budgets = self._allocation_budgets(selected, available, remaining_slots)
        for (intent, signal_id, due_at), budget in zip(selected, budgets):
            if budget <= 0:
                self._reject(intent.symbol, timestamp, "insufficient_cash", signal_id)
                continue
            self.pending.append(PendingPortfolioOrder(intent.symbol, intent.direction, due_at, intent.reason, volume=intent.volume, budget=budget, signal_id=signal_id))
            signal = self._signal(signal_id)
            if signal:
                signal.status = "queued"
                signal.due_at = due_at

    def _prioritize_buys(
        self,
        buys: Sequence[tuple[OrderIntent, int]],
        timestamp: datetime,
    ) -> list[tuple[OrderIntent, int]]:
        rows = [
            {"intent": intent, "signal_id": signal_id, "symbol": intent.symbol, **intent.diagnostic}
            for intent, signal_id in buys
        ]
        ranked = rank_opening_volume_candidates(
            rows,
            mode=self.candidate_sort,
            weights=self.score_weights,
            score_min=self.score_min,
            score_max=self.score_max,
            watchlist_order=self.watchlist_order,
        )
        return [(row["intent"], int(row["signal_id"])) for row in ranked]

    def force_close_at_end(
        self,
        bars_by_symbol: Mapping[str, Sequence[BarData]],
        daily_references: Mapping[str, DailyReference],
    ) -> None:
        """Close eligible remaining positions at the final available minute close."""
        final_bars = {
            symbol: max(bars, key=lambda item: item.datetime)
            for symbol, bars in bars_by_symbol.items() if bars
        }
        if not final_bars:
            return
        self.last_prices.update({symbol: float(bar.close_price) for symbol, bar in final_bars.items() if bar.close_price > 0})
        for symbol, position in list(self.positions.items()):
            if position.volume <= 0:
                continue
            bar = final_bars.get(symbol)
            if bar is None or bar.close_price <= 0 or bar.volume <= 0:
                self._reject(symbol, bar.datetime if bar else datetime.min, "suspended_or_missing_bar")
                continue
            rule = self._rule(symbol)
            if self._violates_limit(Direction.SHORT, float(bar.close_price), daily_references.get(symbol), rule):
                self._reject(symbol, bar.datetime, "price_limit")
                continue
            volume = min(position.volume, self._capacity(bar, rule, Direction.SHORT))
            self._sell(
                PendingPortfolioOrder(symbol, Direction.SHORT, bar.datetime, "end_of_backtest", volume=volume),
                bar,
                volume,
                rule,
                self._equity_at_open(final_bars),
                base_price=float(bar.close_price),
            )
        self._record_equity(max(bar.datetime for bar in final_bars.values()))

    def _allocation_budgets(
        self,
        selected: Sequence[tuple[OrderIntent, int, datetime]],
        available: float,
        remaining_slots: int,
    ) -> list[float]:
        """Allocate this timestamp's investable cash across final selections.

        ``equal`` deliberately ignores a strategy-provided ``target_cash``:
        strategy files express signals only, while the selected front-end
        position-sizing mode exclusively owns portfolio allocation.
        """
        if not selected or available <= 0:
            return [0.0] * len(selected)
        if self.position_sizing == "equal":
            per_slot_budget = self._daily_equal_budget or 0.0
            return [per_slot_budget] * len(selected)

        raw_scores = [self._allocation_score(intent) for intent, _, _ in selected]
        score_total = sum(raw_scores)
        if score_total <= 0:
            return [available / len(selected)] * len(selected)
        return [available * score / score_total for score in raw_scores]

    @staticmethod
    def _allocation_score(intent: OrderIntent) -> float:
        explicit = intent.diagnostic.get("score")
        try:
            score = float(explicit)
        except (TypeError, ValueError):
            score = 0.0
        if score > 0:
            return score
        matched = intent.diagnostic.get("matched_conditions", [])
        return float(len(matched)) if isinstance(matched, (list, tuple)) else 1.0

    def _fill_due(self, timestamp: datetime, bars: Mapping[str, BarData], references: Mapping[str, DailyReference]) -> None:
        released_position_slot = False
        for order in [item for item in self.pending if item.due_at == timestamp]:
            self.pending.remove(order)
            bar = bars.get(order.symbol)
            if bar is None or bar.open_price <= 0 or bar.volume <= 0:
                self._reject(order.symbol, timestamp, "suspended_or_missing_bar", order.signal_id)
                continue
            rule = self._rule(order.symbol)
            reference = references.get(order.symbol)
            if self._violates_limit(order.direction, float(bar.open_price), reference, rule):
                self._reject(order.symbol, timestamp, "price_limit", order.signal_id)
                continue
            capacity = self._capacity(bar, rule, order.direction)
            if order.direction == Direction.LONG:
                volume = order.volume or self._buy_volume(order.budget, float(bar.open_price), rule)
                volume = min(volume, capacity)
                self._buy(order, bar, volume, rule, self._equity_at_open(bars))
            else:
                position = self.positions.get(order.symbol)
                volume = min(order.volume or 0, position.volume if position else 0, capacity)
                self._sell(order, bar, volume, rule, self._equity_at_open(bars))
                released_position_slot = released_position_slot or not (
                    self.positions.get(order.symbol) and self.positions[order.symbol].volume > 0
                )

        # Recalculate after every same-minute fill has completed so later
        # signals see a coherent post-sale cash balance and slot count.
        if released_position_slot:
            self._refresh_daily_equal_budget(bars)

    def _buy(self, order: PendingPortfolioOrder, bar: BarData, volume: int, rule: AShareTradingRule, equity_before: float) -> None:
        if volume < rule.first_buy_minimum:
            self._reject(order.symbol, bar.datetime, "below_minimum_lot", order.signal_id)
            return
        price = float(bar.open_price) * (1 + self.slippage_rate)
        turnover = price * volume
        commission = max(turnover * self.commission_rate, self.min_commission)
        if turnover + commission > self.cash + 1e-8:
            self._reject(order.symbol, bar.datetime, "insufficient_cash", order.signal_id)
            return
        position = self.positions.setdefault(order.symbol, PortfolioPosition(order.symbol))
        combined_cost = position.average_cost * position.volume + turnover + commission
        position.volume += volume
        position.average_cost = combined_cost / position.volume
        position.entry_date = bar.datetime.date()
        position.entry_datetime = bar.datetime
        self.cash -= turnover + commission
        self._fill(PortfolioFill(order.symbol, Direction.LONG, bar.datetime, price, volume, order.reason, commission, 0.0, abs(price - bar.open_price) * volume,
                                 portfolio_equity_before=equity_before,
                                 entry_position_pct=(turnover + commission) / equity_before if equity_before > 0 else None,
                                 signal_id=order.signal_id))

    def _sell(
        self,
        order: PendingPortfolioOrder,
        bar: BarData,
        volume: int,
        rule: AShareTradingRule,
        equity_before: float,
        *,
        base_price: float | None = None,
    ) -> None:
        if volume < rule.lot_size:
            self._reject(order.symbol, bar.datetime, "below_lot_or_no_position", order.signal_id)
            return
        position = self.positions[order.symbol]
        base = float(base_price if base_price is not None else bar.open_price)
        price = base * (1 - self.slippage_rate)
        turnover = price * volume
        commission = max(turnover * self.commission_rate, self.min_commission)
        stamp_tax = turnover * self.stamp_tax_rate
        position.volume -= volume
        if position.volume <= 0:
            position.volume = 0
            position.average_cost = 0.0
            position.entry_date = None
            position.entry_datetime = None
        self.cash += turnover - commission - stamp_tax
        self._fill(PortfolioFill(order.symbol, Direction.SHORT, bar.datetime, price, volume, order.reason, commission, stamp_tax, abs(price - base) * volume,
                                 portfolio_equity_before=equity_before, signal_id=order.signal_id))

    def _record_signal(self, intent: OrderIntent, timestamp: datetime) -> int:
        signal_id = len(self.signals) + 1
        self.signals.append(PortfolioSignal(signal_id, intent.symbol, intent.direction, timestamp, intent.reason, dict(intent.diagnostic)))
        return signal_id

    def _signal(self, signal_id: int | None) -> PortfolioSignal | None:
        return self.signals[signal_id - 1] if signal_id and 0 < signal_id <= len(self.signals) else None

    def _reject(self, symbol: str, timestamp: datetime, reason: str, signal_id: int | None = None) -> None:
        self.rejections.append(PortfolioRejection(symbol, timestamp, reason, signal_id))
        signal = self._signal(signal_id)
        if signal:
            signal.status = "rejected"
            signal.rejection_reason = reason

    def _fill(self, fill: PortfolioFill) -> None:
        self.fills.append(fill)
        signal = self._signal(fill.signal_id)
        if signal:
            signal.status = "filled"
            signal.fill_datetime = fill.datetime

    def _equity_at_open(self, bars: Mapping[str, BarData]) -> float:
        prices = {symbol: float(bar.open_price) for symbol, bar in bars.items() if bar.open_price > 0}
        return self.cash + sum(
            position.volume * prices.get(symbol, self.last_prices.get(symbol, position.average_cost))
            for symbol, position in self.positions.items() if position.volume > 0
        )

    def _rule(self, symbol: str) -> AShareTradingRule:
        return rule_for_symbol(
            symbol,
            name=self.instrument_names.get(symbol),
            limit_pct=self.instrument_limit_pcts.get(symbol),
        )

    def _capacity(self, bar: BarData, rule: AShareTradingRule, direction: Direction) -> int:
        ratio = self.max_buy_volume_ratio if direction == Direction.LONG else self.max_sell_volume_ratio
        if ratio is None:
            return 10**12
        return floor(float(bar.volume) * ratio / rule.lot_size) * rule.lot_size

    def _buy_volume(self, budget: float, open_price: float, rule: AShareTradingRule) -> int:
        effective = open_price * (1 + self.slippage_rate)
        if effective <= 0:
            return 0
        return floor((budget - self.min_commission) / effective / rule.lot_size) * rule.lot_size

    def _violates_limit(self, direction: Direction, price: float, reference: DailyReference | None, rule: AShareTradingRule) -> bool:
        if not reference or not reference.previous_close or reference.previous_close <= 0:
            return False
        if direction == Direction.LONG:
            return price >= reference.previous_close * (1 + rule.price_limit_pct) - 1e-8
        return price <= reference.previous_close * (1 - rule.price_limit_pct) + 1e-8

    def _available_cash(self) -> float:
        reserved = sum(order.budget for order in self.pending if order.direction == Direction.LONG)
        return self.cash - reserved

    def _active_long_symbols(self) -> set[str]:
        return {
            symbol for symbol, position in self.positions.items() if position.volume > 0
        } | {
            order.symbol for order in self.pending if order.direction == Direction.LONG
        }

    def _refresh_daily_equal_budget(self, bars: Mapping[str, BarData]) -> None:
        """Refresh equal sizing with a cash floor based on current equity."""
        remaining_slots = self.max_positions - len(self._active_long_symbols())
        self.reserve_cash = self._equity_at_open(bars) * self.reserve_ratio
        spendable_cash = max(self._available_cash() - self.reserve_cash, 0.0)
        self._daily_equal_budget = spendable_cash / remaining_slots if remaining_slots > 0 else 0.0

    def _record_equity(self, timestamp: datetime) -> None:
        market_value = sum(
            position.volume * self.last_prices.get(symbol, position.average_cost)
            for symbol, position in self.positions.items() if position.volume > 0
        )
        self.equity_curve.append({"date": timestamp.isoformat(sep=" "), "value": round(self.cash + market_value, 2)})
