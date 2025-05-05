import asyncio
import datetime
import json
from enum import StrEnum
from pathlib import Path

from ccxt.async_support import Exchange
from loguru import logger
from pydantic import BaseModel, model_validator

from bitcoin import utils
from bitcoin.conf import settings
from bitcoin.trading.helper import OrderBlockParser
from bitcoin.trading.schema.base import KLine, OrderBlock


class TriggerType(StrEnum):
    loss = "loss"  # 触发止损
    surplus = "surplus"  # 触发止盈
    none = "none"  # 没有触发止盈止损
    unknown = "unknown"  # 无法分辨触发了止盈还是止损(一根k同时触发止盈和止损)


class OrderItem(BaseModel):
    order_block: OrderBlock
    entry_price: float  # 入场价格
    stop_loss_price: float  # 止损价格
    stop_surplus_price: float  # 止盈价格

    def desc_json(self):
        return {
            "time": utils.format_datetime(
                self.order_block.order_block_kline.opening_time
            ),
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

    def trigger(self, kline: KLine) -> TriggerType:
        hit_surplus = False
        hit_loss = False

        if self.order_block.side == "long":
            hit_surplus = kline.highest_price >= self.stop_surplus_price
            hit_loss = kline.lowest_price <= self.stop_loss_price
        elif self.order_block.side == "short":
            hit_surplus = kline.lowest_price <= self.stop_surplus_price
            hit_loss = kline.highest_price >= self.stop_loss_price

        if hit_surplus and hit_loss:
            return TriggerType.unknown
        elif hit_surplus:
            return TriggerType.surplus
        elif hit_loss:
            return TriggerType.loss
        else:
            return TriggerType.none


class OrderManager(object):
    def __init__(self):
        pass


class Tester(object):
    def __init__(
        self,
        symbol: str,
        timeframe: str,
        product_type: str,
        profit: float,
        weekday_profit: float | None = None,
        fee_rate: float = 0.08,
        file: Path | None = None,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.product_type = product_type
        self.exchange: Exchange | None = None
        self.file = file
        self.profit = profit
        self.weekday_profit = weekday_profit or profit
        self.fee_rate = fee_rate
        # 持仓中的订单块订单
        self._opened_orders: list[OrderItem] = []
        self._loss_orders: list[OrderItem] = []
        self._surplus_orders: list[OrderItem] = []
        self._unknown_orders: list[OrderItem] = []

    async def run(self):
        async with settings.create_async_exchange_public(
            "binance"
        ) as self.exchange:
            start = datetime.datetime.strptime(
                "2024-05-05 08:00:00", "%Y-%m-%d %H:%M:%S"
            )
            klines = [
                KLine.from_ccxt(ohlcv)
                for ohlcv in await self.exchange.fetch_ohlcv(
                    self.symbol,
                    self.timeframe,
                    since=int(start.timestamp() * 1000),
                    params={
                        # "until": int(datetime.datetime.now().timestamp() * 1000),
                        "paginate": True,
                        "paginationCalls": 20,
                    },
                )
            ]
            logger.info(
                f"backtesting {self.symbol}-{self.timeframe}: {klines[0].opening_time} - {klines[-1].opening_time}"
            )
            await self.resolve(klines)
            await self.report()

    async def report(self):
        loss_rate = sum(order.stop_loss_rate for order in self._loss_orders)
        surplus_rate = sum(
            order.stop_surplus_rate for order in self._surplus_orders
        )
        order_cont = len(self._loss_orders) + len(self._surplus_orders)
        fee = self.fee_rate * order_cont
        logger.info(
            f"======== backtesting result ("
            f"手续费: {self.fee_rate}, "
            f"工作日盈利: {self.profit * 100}%, "
            f"周末盈利: {self.weekday_profit * 100}%"
            f")========"
        )
        logger.info(f"{order_cont}单手续费: {fee}%")
        logger.info(f"止损: {len(self._loss_orders)}, 共{loss_rate}%")
        logger.info(f"止盈: {len(self._surplus_orders)}, 共{surplus_rate}%")
        logger.info(f"无法分辨: {len(self._unknown_orders)}")
        logger.info(f"持仓: {len(self._opened_orders)}")
        if self.file:
            with open(self.file, "w", encoding="utf-8") as fp:
                data = {
                    "time": utils.format_datetime(datetime.datetime.now()),
                    "fee": fee,
                    "fee_rat": self.fee_rate,
                    "symbol": self.symbol,
                    "timeframe": self.timeframe,
                    "profit": self.profit,
                    "weekday_profit": self.weekday_profit,
                    "product_type": self.product_type,
                    "loss_rate": loss_rate,
                    "surplus_rate": surplus_rate,
                    "orders": {
                        "stop_loss": [
                            order.desc_json() for order in self._loss_orders
                        ],
                        "stop_surplus": [
                            order.desc_json() for order in self._surplus_orders
                        ],
                        "unknown": [
                            order.desc_json() for order in self._unknown_orders
                        ],
                        "open": [
                            order.desc_json() for order in self._opened_orders
                        ],
                    },
                }
                json.dump(data, fp)

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
        kline = order_block.order_block_kline
        if utils.is_workday(test_kline.opening_time):
            profit_rate = self.profit
        else:
            profit_rate = self.weekday_profit
        if order_block.side == "long":
            order_item = OrderItem(
                order_block=order_block,
                entry_price=kline.highest_price,
                stop_loss_price=kline.lowest_price,
                stop_surplus_price=kline.highest_price * (1 + profit_rate),
            )
        else:
            order_item = OrderItem(
                order_block=order_block,
                entry_price=kline.lowest_price,
                stop_loss_price=kline.highest_price,
                stop_surplus_price=kline.lowest_price * (1 - profit_rate),
            )
        self._opened_orders.append(order_item)

    def _process_sl_or_tp(self, kline: KLine):
        remaining_open_orders: list[OrderItem] = []
        for item in self._opened_orders:
            trigger_type = item.trigger(kline)
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
                    raise RuntimeError(f"unknown item {trigger_type}")
        self._opened_orders = remaining_open_orders


if __name__ == "__main__":
    backtesting_path = settings.project_path / "backtesting"
    backtesting_path.mkdir(exist_ok=True)
    tester = Tester(
        "FIL/USDT:USDT",
        timeframe="30m",
        product_type="USDT-FUTURES",
        profit=0.02,
        weekday_profit=0.005,
        file=backtesting_path / "fil_2_05.json",
    )
    asyncio.run(tester.run())
