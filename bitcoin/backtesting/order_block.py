import asyncio
import datetime
import json
from enum import StrEnum
from pathlib import Path
from typing import assert_never

import pandas as pd
import pendulum  # noqa: F401
from ccxt.async_support import Exchange
from ccxt.base.types import PositionSide
from loguru import logger
from pydantic import BaseModel, model_validator

from bitcoin import utils
from bitcoin.backtesting.fetcher import KLine1MFetcher, init_db
from bitcoin.conf import settings
from bitcoin.trading.helper import OrderBlockParser
from bitcoin.trading.schema.base import KLine, OrderBlock


class TriggerType(StrEnum):
    loss = "loss"  # 触发止损
    surplus = "surplus"  # 触发止盈
    # 无法判断止盈止损
    unknown = "unknown"
    none = "none"  # 没有触发止盈止损


class OrderItem(BaseModel):
    order_block: OrderBlock
    entry_kline: KLine  # 入场k
    entry_price: float  # 入场价格
    stop_loss_price: float  # 止损价格
    stop_surplus_price: float  # 止盈价格
    trigger_type: TriggerType = TriggerType.none
    trigger_kline: KLine | None = None  # 触发止盈或者止损的k线

    def model_dump_for_report(self):
        if self.trigger_kline:
            trigger_time = utils.format_datetime(
                self.trigger_kline.opening_time
            )
        else:
            trigger_time = None
        return {
            "time": utils.format_datetime(
                self.order_block.order_block_kline.opening_time
            ),
            "trigger_time": trigger_time,
            "side": self.order_block.side,
            "entry_price": self.entry_price,
            "stop_loss_price": self.stop_loss_price,
            "stop_surplus_price": self.stop_surplus_price,
            "stop_loss_rate": self.stop_loss_rate,
            "stop_surplus_rate": self.stop_surplus_rate,
        }

    @property
    def stop_loss_rate(self) -> float:
        return utils.get_undulate_percent(
            self.entry_price, self.stop_loss_price
        )

    @property
    def stop_surplus_rate(self) -> float:
        return utils.get_undulate_percent(
            self.entry_price, self.stop_surplus_price
        )

    @model_validator(mode="after")
    def validate_price(self):
        if self.order_block.side == "long":
            if not (
                self.stop_loss_price
                <= self.entry_price
                <= self.stop_surplus_price
            ):
                raise ValueError(
                    f"Invalid prices for LONG: SL({self.stop_loss_price}) "
                    f"<= Entry({self.entry_price}) "
                    f"<= TP({self.stop_surplus_price}) condition not met."
                )
        elif self.order_block.side == "short":
            if not (
                self.stop_surplus_price
                <= self.entry_price
                <= self.stop_loss_price
            ):
                raise ValueError(
                    f"Invalid prices for SHORT: TP({self.stop_surplus_price}) "
                    f"<= Entry({self.entry_price}) "
                    f"<= SL({self.stop_loss_price}) condition not met."
                )
        return self


class Tester(object):
    def __init__(
        self,
        symbol: str,
        timeframe: str,
        product_type: str,
        profit: float,
        sides: list[PositionSide] = ("long", "short"),
        weekday_profit: float | None = None,
        fee_rate: float = 0.08,
        start: str | None = None,
        until: str | None = None,
        file: Path | None = None,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.product_type = product_type
        self.exchange: Exchange | None = None
        self._fetcher_1m: KLine1MFetcher | None = None
        self.file = file
        self.profit = profit
        self.weekday_profit = weekday_profit or profit
        self.fee_rate = fee_rate
        self.start = start
        self.until = until
        timeframe_seconds = Exchange.parse_timeframe(timeframe)
        assert timeframe_seconds > 60
        self._1m_delta = datetime.timedelta(seconds=timeframe_seconds - 60)
        self.sides = tuple(set(sides or ()))
        # 持仓中的订单块订单
        self._opened_orders: list[OrderItem] = []
        self._loss_orders: list[OrderItem] = []
        self._surplus_orders: list[OrderItem] = []

        # 入场就触发了止盈, 而且收盘价不是止盈, 并且后续没触发止损的订单.
        self._unknown_orders: list[OrderItem] = []

        self._df: pd.DataFrame | None = None

    async def _load_1m_df(
        self, start: datetime.datetime, until: datetime.datetime
    ):
        assert self._fetcher_1m
        klines = await self._fetcher_1m.fetch_klines(start, until)
        df = pd.DataFrame(
            tuple(
                {"time": kline.opening_time, "kline": kline}
                for kline in klines
            )
        )
        df.set_index("time", inplace=True)
        df.sort_index(inplace=True)
        self._df = df

    async def run(self):
        await init_db()
        async with settings.create_async_exchange_public(
            "binance"
        ) as self.exchange:
            self._fetcher_1m = KLine1MFetcher(
                symbol=self.symbol, exchange=self.exchange
            )
            start = datetime.datetime.strptime(
                self.start, "%Y-%m-%d %H:%M:%S"
            ).astimezone(settings.zone_info)
            if not self.until:
                until = datetime.datetime.now(tz=settings.zone_info)
            else:
                until = datetime.datetime.strptime(
                    self.until, "%Y-%m-%d %H:%M:%S"
                ).astimezone(settings.zone_info)

            load_df_fut = asyncio.create_task(self._load_1m_df(start, until))
            klines = [
                KLine.from_ccxt(ohlcv)
                for ohlcv in await self.exchange.fetch_ohlcv(
                    self.symbol,
                    self.timeframe,
                    since=int(start.timestamp() * 1000),
                    params={
                        "until": int(until.timestamp() * 1000),
                        "paginate": True,
                        "paginationCalls": 200000,
                    },
                )
            ]
            await load_df_fut
            logger.info(
                f"backtesting {self.symbol}-{self.timeframe}: "
                f"{klines[0].opening_time} - {klines[-1].opening_time}"
            )
            await self.resolve(klines)
            await self.report()

        from tortoise import Tortoise

        await Tortoise.close_connections()

    def get_kline_1m_detail(
        self, opening_time: datetime.datetime
    ) -> list[KLine]:
        assert self._df is not None
        result = list(
            self._df[opening_time : opening_time + self._1m_delta]["kline"]
        )
        return result

    async def report(self):
        loss_rate = sum(order.stop_loss_rate for order in self._loss_orders)
        surplus_rate = sum(
            order.stop_surplus_rate for order in self._surplus_orders
        )
        unknown_loss_rate = sum(
            order.stop_loss_rate for order in self._unknown_orders
        )
        unknown_surplus_rate = sum(
            order.stop_surplus_rate for order in self._unknown_orders
        )
        order_cont = len(self._loss_orders) + len(self._surplus_orders)
        fee = self.fee_rate * order_cont
        maybe_profit1 = surplus_rate - (loss_rate + unknown_loss_rate + fee)
        maybe_profit2 = (surplus_rate + unknown_surplus_rate) - (
            loss_rate + fee
        )
        logger.info(
            f"======== backtesting result ("
            f"手续费: {self.fee_rate}%, "
            f"工作日盈利: {self.profit * 100}%, "
            f"周末盈利: {self.weekday_profit * 100}%"
            f")========"
        )
        logger.info(f"{order_cont}单手续费: {fee}%")
        logger.info(f"止损: {len(self._loss_orders)}, 共{loss_rate}%")
        logger.info(f"止盈: {len(self._surplus_orders)}, 共{surplus_rate}%")
        logger.info(
            f"无法分辨: {len(self._unknown_orders)}, "
            f"(-{unknown_loss_rate}% - {unknown_surplus_rate}%)"
        )
        logger.info(f"持仓中 {len(self._opened_orders)}")
        logger.info(f"总收益率: ({maybe_profit1}% - {maybe_profit2}%)")
        if self.file:
            with open(self.file, "w", encoding="utf-8") as fp:
                data = {
                    "time": utils.format_datetime(datetime.datetime.now()),
                    "symbol": self.symbol,
                    "timeframe": self.timeframe,
                    "product_type": self.product_type,
                    "fee": f"{fee}%",
                    "fee_rate": f"{self.fee_rate}%",
                    "profit": f"{self.profit * 100}%",
                    "weekday_profit": f"{self.weekday_profit * 100}%",
                    "loss_rate": f"-{loss_rate}%",
                    "surplus_rate": f"+{surplus_rate}%",
                    "unknown_loss_rate": f"-{unknown_loss_rate}%",
                    "unknown_surplus_rate": f"+{unknown_surplus_rate}%",
                    "total_profit_rate": f"{maybe_profit1}% - {maybe_profit2}%",
                    "orders": {
                        "stop_loss": [
                            order.model_dump_for_report()
                            for order in self._loss_orders
                        ],
                        "stop_surplus": [
                            order.model_dump_for_report()
                            for order in self._surplus_orders
                        ],
                        "unknown": [
                            order.model_dump_for_report()
                            for order in self._unknown_orders
                        ],
                        "open": [
                            order.model_dump_for_report()
                            for order in self._opened_orders
                        ],
                    },
                }
                json.dump(data, fp, ensure_ascii=False)
                logger.info(
                    f"save backtesting detail at {self.file.absolute()}"
                )

    async def resolve(self, klines: list[KLine]):
        order_block_parser = OrderBlockParser(timeframe=self.timeframe)

        # 持仓中的订单块仓位
        for kline in klines:
            result = order_block_parser.fetch(kline)
            try:
                if not result or result.update_order_block:
                    continue

                if result.tested_order_blocks:
                    for order_block in result.tested_order_blocks:
                        self._open_order(order_block, kline)
            finally:
                self._process_sl_or_tp(kline)

    def _open_order(self, order_block: OrderBlock, test_kline: KLine):
        if order_block.side not in self.sides:
            return

        kline = order_block.order_block_kline
        # if pendulum.instance(test_kline.opening_time) >= pendulum.instance(
        #     kline.opening_time
        # ).add(months=1):
        #     logger.debug(
        #         f"{kline.opening_time} tested at {test_kline.opening_time}"
        #     )
        #     return
        if utils.is_workday(test_kline.opening_time):
            profit_rate = self.profit
        else:
            profit_rate = self.weekday_profit
        if order_block.side == "long":
            order_item = OrderItem(
                order_block=order_block,
                entry_kline=test_kline,
                entry_price=kline.highest_price,
                stop_loss_price=kline.lowest_price,
                stop_surplus_price=kline.highest_price * (1 + profit_rate),
            )
        else:
            order_item = OrderItem(
                order_block=order_block,
                entry_kline=test_kline,
                entry_price=kline.lowest_price,
                stop_loss_price=kline.highest_price,
                stop_surplus_price=kline.lowest_price * (1 - profit_rate),
            )
        self._opened_orders.append(order_item)

    def _process_sl_or_tp(self, kline: KLine):
        remaining_open_orders: list[OrderItem] = []
        for item in self._opened_orders:
            trigger_type = self.trigger(item, kline)
            item.trigger_type = trigger_type
            match trigger_type:
                case TriggerType.loss:
                    self._loss_orders.append(item)
                case TriggerType.surplus:
                    self._surplus_orders.append(item)
                case TriggerType.unknown:
                    self._unknown_orders.append(item)
                case TriggerType.none:
                    remaining_open_orders.append(item)
                case _:
                    assert_never(trigger_type)
            if trigger_type not in (TriggerType.none, TriggerType.unknown):
                if not item.trigger_kline:
                    item.trigger_kline = kline
        self._opened_orders = remaining_open_orders

    def trigger(self, order: OrderItem, kline: KLine) -> TriggerType:
        hit_surplus, hit_loss = self._parse_surplus_and_loss_hit(order, kline)
        if hit_surplus and hit_loss:
            return self._process_1m_trigger(order, kline)
        elif hit_surplus:
            side = order.order_block.side
            if kline.opening_time == order.entry_kline.opening_time:
                # 入场k就触发了止盈, 这种不可以保证一定是止盈
                if (
                    side == "long"
                    and kline.closing_price >= order.stop_surplus_price
                ) or (
                    side == "short"
                    and kline.closing_price <= order.stop_surplus_price
                ):
                    # 如果收盘价格是止盈, 那么一定是止盈的
                    return TriggerType.surplus
                return self._process_1m_trigger(order, order.entry_kline)
            return TriggerType.surplus
        elif hit_loss:
            return TriggerType.loss
        else:
            return TriggerType.none

    def _process_1m_trigger(
        self, order: OrderItem, entry_kline: KLine
    ) -> TriggerType:
        # 是否入场
        is_entry = False
        klines = self.get_kline_1m_detail(entry_kline.opening_time)
        for kline in klines:
            first_entry = False
            if not is_entry:
                is_entry = (
                    kline.lowest_price
                    <= order.entry_price
                    <= kline.highest_price
                )
                if not is_entry:
                    continue
                first_entry = True

            hit_surplus, hit_loss = self._parse_surplus_and_loss_hit(
                order, kline
            )
            if hit_surplus and hit_loss:
                # 1分钟k也出现了同时止盈止损, 确定无法分辨
                order.trigger_kline = kline
                return TriggerType.unknown
            elif hit_surplus:
                # 入场k就是止盈, 不判断直接无法分辨. 不再判断收盘价等信息了
                order.trigger_kline = kline
                if first_entry:
                    return TriggerType.unknown
                return TriggerType.surplus
            elif hit_loss:
                order.trigger_kline = kline
                return TriggerType.loss
        if entry_kline == order.entry_kline:
            return TriggerType.none
        assert_never(
            (
                order.order_block.order_block_kline.opening_time,
                entry_kline.opening_time,
            )
        )

    @staticmethod
    def _parse_surplus_and_loss_hit(order: OrderItem, kline: KLine):
        hit_surplus = False
        hit_loss = False
        side = order.order_block.side
        if side == "long":
            hit_surplus = kline.highest_price >= order.stop_surplus_price
            hit_loss = kline.lowest_price <= order.stop_loss_price
        elif side == "short":
            hit_surplus = kline.lowest_price <= order.stop_surplus_price
            hit_loss = kline.highest_price >= order.stop_loss_price
        return hit_surplus, hit_loss


if __name__ == "__main__":
    backtesting_path = settings.project_path / "backtesting"
    backtesting_path.mkdir(exist_ok=True)
    tester = Tester(
        "BTC/USDT:USDT",
        timeframe="30m",
        product_type="USDT-FUTURES",
        profit=1 / 100,
        weekday_profit=0.5 / 100,
        sides=["long", "short"],  # 方向
        start="2024-05-04 08:00:00",  # 回测开始时间
        file=backtesting_path / "backtesting.json",
    )
    asyncio.run(tester.run())
