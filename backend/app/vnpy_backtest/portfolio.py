"""A deterministic, multi-symbol minute portfolio executor using vn.py bars."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from math import floor
from typing import Iterable, Mapping, Sequence

from vnpy.trader.constant import Direction
from vnpy.trader.object import BarData

from app.vnpy_backtest.market_rules import AShareTradingRule, price_limit_bounds, rule_for_symbol
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
    reserves_slot: bool = True
    candidate_batch: datetime | None = None


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

    def __init__(self, signal_projector=None) -> None:
        self._history: dict[str, list[dict]] = defaultdict(list)
        self._signal_projector = signal_projector

    def references(
        self,
        reference_day: date | None = None,
        symbols: Iterable[str] | None = None,
        execution_metadata: Mapping[str, Mapping[str, object]] | None = None,
    ) -> dict[str, DailyReference]:
        """Return references only for symbols that trade on ``reference_day``.

        A suspended security has neither a minute bar nor a daily adjustment
        factor for the suspended date.  It must be omitted from the signal
        context, rather than forcing a factor lookup for a price that does not
        exist.  A symbol that *does* trade today still has to have complete
        factor coverage, so genuine data gaps remain visible as errors.
        """
        result: dict[str, DailyReference] = {}
        allowed = set(symbols) if symbols is not None else None
        for symbol, rows in self._history.items():
            if allowed is not None and symbol not in allowed:
                continue
            if not rows:
                continue
            previous = rows[-1]
            effective_reference_day = reference_day or previous["date"]
            metadata = (execution_metadata or {}).get(symbol, {})

            def adjusted(row: dict, field: str) -> float:
                value = float(row[field])
                if self._signal_projector is None:
                    return value
                return value * self._signal_projector.scale(symbol, row["date"], effective_reference_day)

            raw_previous_close = float(previous["close"])
            try:
                metadata_pre_close = float(metadata.get("pre_close", 0))
            except (TypeError, ValueError):
                metadata_pre_close = 0.0
            try:
                metadata_limit_pct = float(metadata.get("price_limit_pct", 0))
            except (TypeError, ValueError):
                metadata_limit_pct = 0.0
            result[symbol] = DailyReference(
                previous_open=adjusted(previous, "open"),
                previous_close=adjusted(previous, "close"),
                previous_high=adjusted(previous, "high"),
                previous_low=adjusted(previous, "low"),
                previous_volume=previous["volume"],
                raw_previous_close=raw_previous_close,
                limit_reference_price=metadata_pre_close if metadata_pre_close > 0 else raw_previous_close,
                price_limit_pct=metadata_limit_pct if metadata_limit_pct > 0 else None,
                closes=tuple(adjusted(row, "close") for row in rows[-5:]),
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
                    "date": symbol_bars[0].datetime.date(),
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
        max_volume_ratio: float | None = 0.10,
        max_positions: int = 10,
        position_sizing: str = "equal",
        reserve_ratio: float = 0.03,
        instrument_names: Mapping[str, str] | None = None,
        instrument_tick_sizes: Mapping[str, float] | None = None,
    ) -> None:
        self.initial_cash = float(initial_cash)
        self.cash = self.initial_cash
        self.commission_rate = float(commission_rate)
        self.stamp_tax_rate = float(stamp_tax_rate)
        self.slippage_rate = float(slippage_rate)
        self.min_commission = float(min_commission)
        self.max_volume_ratio = max_volume_ratio
        self.max_positions = max(1, int(max_positions))
        if position_sizing not in {"equal", "score_weight"}:
            raise ValueError("position_sizing must be equal or score_weight")
        self.position_sizing = position_sizing
        self.reserve_ratio = max(float(reserve_ratio), 0.0)
        # The reserve is calculated from currently available cash whenever a
        # new equal-size budget is established (day start or a completed exit).
        self._daily_equal_budget: float | None = None
        self.instrument_names = dict(instrument_names or {})
        self.instrument_tick_sizes = dict(instrument_tick_sizes or {})
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
    ) -> None:
        by_timestamp: dict[datetime, dict[str, BarData]] = defaultdict(dict)
        next_times: dict[tuple[str, datetime], datetime] = {}
        for symbol, bars in bars_by_symbol.items():
            ordered = sorted(bars, key=lambda item: item.datetime)
            for index, bar in enumerate(ordered):
                by_timestamp[bar.datetime][symbol] = bar
                if index + 1 < len(ordered):
                    next_times[(symbol, bar.datetime)] = ordered[index + 1].datetime

        # Equal sizing is snapshotted for the day.  Later buys keep this
        # amount unless a complete exit releases both cash and a position slot.
        self._refresh_daily_equal_budget()
        for timestamp in sorted(by_timestamp):
            bars = by_timestamp[timestamp]
            self._fill_due(timestamp, bars, daily_references)
            self.last_prices.update({symbol: float(bar.close_price) for symbol, bar in bars.items() if bar.close_price > 0})
            context = PortfolioContext(
                timestamp=timestamp,
                cash=self.cash,
                reserved_cash=sum(
                    order.budget
                    for order in self.pending
                    if order.direction == Direction.LONG and order.reserves_slot
                ),
                positions={
                    symbol: PortfolioPositionView(symbol, position.volume, position.average_cost, position.entry_date)
                    for symbol, position in self.positions.items() if position.volume > 0
                },
                daily_references=daily_references,
            )
            intents = list(strategy.on_minute(bars, context))
            self._queue_intents(intents, timestamp, next_times, context)
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

        # New positions are chosen by matched-condition count, then symbol.
        active_symbols = {
            symbol for symbol, position in self.positions.items() if position.volume > 0
        } | {
            order.symbol
            for order in self.pending
            if order.direction == Direction.LONG and order.reserves_slot
        }
        occupied_slots = len(active_symbols)
        candidates: list[tuple[OrderIntent, int, datetime]] = []
        for intent, signal_id in self._prioritize_buys(buys):
            due_at = next_times.get((intent.symbol, timestamp))
            if due_at is None:
                self._reject(intent.symbol, timestamp, "no_next_bar", signal_id)
                continue
            if intent.symbol in active_symbols:
                self._reject(intent.symbol, timestamp, "already_held", signal_id)
                continue
            candidates.append((intent, signal_id, due_at))

        available = max(context.available_cash * (1 - self.reserve_ratio), 0.0)
        remaining_slots = max(self.max_positions - occupied_slots, 0)
        selected = candidates[:remaining_slots]
        budgets = self._allocation_budgets(selected, available, remaining_slots)
        queued_primaries: list[tuple[OrderIntent, int, datetime, float]] = []
        for (intent, signal_id, due_at), budget in zip(selected, budgets):
            if budget <= 0:
                self._reject(intent.symbol, timestamp, "insufficient_cash", signal_id)
                continue
            queued_primaries.append((intent, signal_id, due_at, budget))
            self.pending.append(PendingPortfolioOrder(
                intent.symbol, intent.direction, due_at, intent.reason,
                volume=intent.volume, budget=budget, signal_id=signal_id,
                reserves_slot=True, candidate_batch=timestamp,
            ))
            signal = self._signal(signal_id)
            if signal:
                signal.status = "queued"
                signal.due_at = due_at

        # 保留同一分钟的其余候选作为候补。它们不预占仓位或资金，只在同一
        # 撮合时点的主候选失败后，按既定优先级顺序依次尝试。
        primary_due_times = {due_at for _, _, due_at, _ in queued_primaries}
        if primary_due_times:
            for intent, signal_id, due_at in candidates[remaining_slots:]:
                if due_at not in primary_due_times:
                    self._reject(intent.symbol, timestamp, "max_positions", signal_id)
                    continue
                self.pending.append(PendingPortfolioOrder(
                    intent.symbol, intent.direction, due_at, intent.reason,
                    volume=intent.volume, budget=0.0, signal_id=signal_id,
                    reserves_slot=False, candidate_batch=timestamp,
                ))
                signal = self._signal(signal_id)
                if signal:
                    signal.status = "queued"
                    signal.due_at = due_at
        else:
            for intent, signal_id, _ in candidates[remaining_slots:]:
                self._reject(intent.symbol, timestamp, "max_positions", signal_id)

    def _prioritize_buys(
        self,
        buys: Sequence[tuple[OrderIntent, int]],
    ) -> list[tuple[OrderIntent, int]]:
        groups: dict[int, list[tuple[OrderIntent, int]]] = defaultdict(list)
        for item in buys:
            matched = item[0].diagnostic.get("matched_conditions", [])
            priority = len(matched) if isinstance(matched, (list, tuple)) else 0
            groups[priority].append(item)
        ordered: list[tuple[OrderIntent, int]] = []
        for priority in sorted(groups, reverse=True):
            # 同优先级候选按股票代码升序选择，避免数据源顺序或随机数影响。
            group = sorted(groups[priority], key=lambda item: (item[0].symbol, item[1]))
            ordered.extend(group)
        return ordered

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
        due_orders = [item for item in self.pending if item.due_at == timestamp]
        for order in due_orders:
            self.pending.remove(order)

        released_position_slot = False
        # 先处理卖单，使同一分钟随后撮合的买单看到最新现金和仓位。
        for order in (item for item in due_orders if item.direction == Direction.SHORT):
            bar = bars.get(order.symbol)
            if bar is None or bar.open_price <= 0 or bar.volume <= 0:
                self._reject(order.symbol, timestamp, "suspended_or_missing_bar", order.signal_id)
                continue
            reference = references.get(order.symbol)
            rule = self._rule(order.symbol, reference, timestamp.date())
            limits = self._price_limits(reference, rule)
            if self._violates_limit(order.direction, float(bar.open_price), limits):
                self._reject(order.symbol, timestamp, "price_limit", order.signal_id)
                continue
            capacity = self._capacity(bar, rule)
            position = self.positions.get(order.symbol)
            volume = min(order.volume or 0, position.volume if position else 0, capacity)
            self._sell(
                order,
                bar,
                volume,
                rule,
                self._equity_at_open(bars),
                lower_limit=limits[1] if limits else None,
            )
            released_position_slot = released_position_slot or not (
                self.positions.get(order.symbol) and self.positions[order.symbol].volume > 0
            )

        # 每个信号批次先尝试占位的主候选；主候选失败后，其预算和仓位
        # 立即交给同批次的下一名候补，在当前开盘撮合时点继续尝试。
        buy_groups: dict[datetime | None, list[PendingPortfolioOrder]] = defaultdict(list)
        for order in due_orders:
            if order.direction == Direction.LONG:
                buy_groups[order.candidate_batch].append(order)
        for batch in sorted(buy_groups, key=lambda item: item or datetime.min):
            orders = buy_groups[batch]
            primaries = [order for order in orders if order.reserves_slot]
            fallbacks = [order for order in orders if not order.reserves_slot]
            reusable_budgets: list[float] = []
            for order in primaries:
                if not self._try_buy_order(order, order.budget, timestamp, bars, references):
                    reusable_budgets.append(order.budget)
            for order in fallbacks:
                if not reusable_budgets:
                    self._reject(order.symbol, timestamp, "max_positions", order.signal_id)
                    continue
                if self._try_buy_order(order, reusable_budgets[0], timestamp, bars, references):
                    reusable_budgets.pop(0)

        # Recalculate after every same-minute fill has completed so later
        # signals see a coherent post-sale cash balance and slot count.
        if released_position_slot:
            self._refresh_daily_equal_budget()

    def _try_buy_order(
        self,
        order: PendingPortfolioOrder,
        budget: float,
        timestamp: datetime,
        bars: Mapping[str, BarData],
        references: Mapping[str, DailyReference],
    ) -> bool:
        if len(self._active_long_symbols()) >= self.max_positions:
            self._reject(order.symbol, timestamp, "max_positions", order.signal_id)
            return False
        if self.positions.get(order.symbol) and self.positions[order.symbol].volume > 0:
            self._reject(order.symbol, timestamp, "already_held", order.signal_id)
            return False
        bar = bars.get(order.symbol)
        if bar is None or bar.open_price <= 0 or bar.volume <= 0:
            self._reject(order.symbol, timestamp, "suspended_or_missing_bar", order.signal_id)
            return False
        reference = references.get(order.symbol)
        rule = self._rule(order.symbol, reference, timestamp.date())
        limits = self._price_limits(reference, rule)
        if self._violates_limit(Direction.LONG, float(bar.open_price), limits):
            self._reject(order.symbol, timestamp, "price_limit", order.signal_id)
            return False
        capacity = self._capacity(bar, rule)
        volume = order.volume or self._buy_volume(budget, float(bar.open_price), rule)
        volume = min(volume, capacity)
        order.budget = budget
        return self._buy(
            order,
            bar,
            volume,
            rule,
            self._equity_at_open(bars),
            upper_limit=limits[0] if limits else None,
        )

    def _buy(
        self,
        order: PendingPortfolioOrder,
        bar: BarData,
        volume: int,
        rule: AShareTradingRule,
        equity_before: float,
        *,
        upper_limit: float | None = None,
    ) -> bool:
        if volume < rule.first_buy_minimum:
            self._reject(order.symbol, bar.datetime, "below_minimum_lot", order.signal_id)
            return False
        price = float(bar.open_price) * (1 + self.slippage_rate)
        if upper_limit is not None:
            price = min(price, upper_limit)
        turnover = price * volume
        commission = max(turnover * self.commission_rate, self.min_commission)
        if turnover + commission > self.cash + 1e-8:
            self._reject(order.symbol, bar.datetime, "insufficient_cash", order.signal_id)
            return False
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
        return True

    def _sell(
        self,
        order: PendingPortfolioOrder,
        bar: BarData,
        volume: int,
        rule: AShareTradingRule,
        equity_before: float,
        *,
        lower_limit: float | None = None,
    ) -> None:
        if volume < rule.lot_size:
            self._reject(order.symbol, bar.datetime, "below_lot_or_no_position", order.signal_id)
            return
        position = self.positions[order.symbol]
        price = float(bar.open_price) * (1 - self.slippage_rate)
        if lower_limit is not None:
            price = max(price, lower_limit)
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
        self._fill(PortfolioFill(order.symbol, Direction.SHORT, bar.datetime, price, volume, order.reason, commission, stamp_tax, abs(price - bar.open_price) * volume,
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

    def _rule(
        self,
        symbol: str,
        reference: DailyReference | None = None,
        trading_day: date | None = None,
    ) -> AShareTradingRule:
        return rule_for_symbol(
            symbol,
            name=self.instrument_names.get(symbol),
            trading_day=trading_day,
            limit_pct=reference.price_limit_pct if reference else None,
            tick_size=self.instrument_tick_sizes.get(symbol),
        )

    def _capacity(self, bar: BarData, rule: AShareTradingRule) -> int:
        if self.max_volume_ratio is None:
            return 10**12
        return floor(float(bar.volume) * self.max_volume_ratio / rule.lot_size) * rule.lot_size

    def _buy_volume(self, budget: float, open_price: float, rule: AShareTradingRule) -> int:
        effective = open_price * (1 + self.slippage_rate)
        if effective <= 0:
            return 0
        return floor((budget - self.min_commission) / effective / rule.lot_size) * rule.lot_size

    @staticmethod
    def _price_limits(
        reference: DailyReference | None,
        rule: AShareTradingRule,
    ) -> tuple[float, float] | None:
        if not reference:
            return None
        previous_close = reference.limit_reference_price or reference.previous_close
        if not previous_close or previous_close <= 0:
            return None
        return price_limit_bounds(previous_close, rule)

    @staticmethod
    def _violates_limit(
        direction: Direction,
        price: float,
        limits: tuple[float, float] | None,
    ) -> bool:
        if limits is None:
            return False
        upper_limit, lower_limit = limits
        if direction == Direction.LONG:
            return price >= upper_limit - 1e-8
        return price <= lower_limit + 1e-8

    def _available_cash(self) -> float:
        reserved = sum(
            order.budget
            for order in self.pending
            if order.direction == Direction.LONG and order.reserves_slot
        )
        return self.cash - reserved

    def _active_long_symbols(self) -> set[str]:
        return {
            symbol for symbol, position in self.positions.items() if position.volume > 0
        } | {
            order.symbol
            for order in self.pending
            if order.direction == Direction.LONG and order.reserves_slot
        }

    def _refresh_daily_equal_budget(self) -> None:
        """Refresh equal sizing at day start or after a complete position exit.

        Buying never refreshes the amount: all queued entries keep the most
        recently established cap.  A completed sale is the only intraday event
        that changes both available cash and an open position slot.
        """
        remaining_slots = self.max_positions - len(self._active_long_symbols())
        spendable_cash = max(self._available_cash() * (1 - self.reserve_ratio), 0.0)
        self._daily_equal_budget = spendable_cash / remaining_slots if remaining_slots > 0 else 0.0

    def _record_equity(self, timestamp: datetime) -> None:
        market_value = sum(
            position.volume * self.last_prices.get(symbol, position.average_cost)
            for symbol, position in self.positions.items() if position.volume > 0
        )
        self.equity_curve.append({"date": timestamp.isoformat(sep=" "), "value": round(self.cash + market_value, 2)})
