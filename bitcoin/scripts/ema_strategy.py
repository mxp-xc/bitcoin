# -*- coding: utf-8 -*-
import asyncio
import datetime
from enum import StrEnum

from ccxt.async_support import Exchange
from talipp.indicators import EMA

from bitcoin import utils
from bitcoin.conf import settings
from bitcoin.trading.helper import KLineWatcher, KLineWrapper
from bitcoin.trading.schema.base import KLine
from bitcoin.watcher.base import BaseWatcher, WatcherManager


class CrossType(StrEnum):
    above = "above"
    """上穿"""

    below = "below"
    """下穿"""

    none = "none"
    """无"""


def _resolve_cross(value: float, prev_price: float, price: float) -> CrossType:
    if prev_price == price:
        return CrossType.none
    if prev_price < value < price:
        return CrossType.above

    if price < value < prev_price:
        return CrossType.below
    return CrossType.none


class EMAWatcher(BaseWatcher):
    def __init__(
        self,
        kline_watcher: KLineWatcher,
        symbol: str,
        timeframe: str,
        wx_key: str | None = None,
    ):
        super().__init__(kline_watcher, symbol, timeframe, wx_key)
        self.ema60 = EMA(60)
        self.ema200 = EMA(200)

    def _available(self):
        return self.ema200 and self.ema200[-1] is not None

    def _ema_add(self, value):
        self.ema60.add(value)
        self.ema200.add(value)

    def _ema_remove(self):
        self.ema60.remove()
        self.ema200.remove()

    def _remove_indicator(self, wrapper: KLineWrapper):
        if not wrapper.closed:
            self._ema_remove()

    async def watch(self):
        # 是否一直处于穿过状态
        prev_wrapper: KLineWrapper | None = None
        async for wrapper in self.kline_watcher.async_iter(
            self.symbol, self.timeframe
        ):
            kline = wrapper.kline
            self._ema_add(kline.closing_price)
            if prev_wrapper and self._available():
                await self._process(
                    prev_wrapper.kline, kline, _initialize=wrapper.initialize
                )

            self._remove_indicator(wrapper)
            prev_wrapper = wrapper

    async def _process(
        self, prev_kline: KLine, kline: KLine, _initialize: bool
    ):
        if prev_kline.closing_price == kline.closing_price:
            # 价格没有变化不处理
            return
        ema60, ema200 = self.ema60[-1], self.ema200[-1]
        if utils.format_datetime(kline.opening_time) == "2025-05-03 13:04:00":
            print(kline)
        cross_type = _resolve_cross(
            ema200, prev_kline.closing_price, kline.closing_price
        )

        if cross_type == CrossType.above:
            await self._send_wx(
                kline.opening_time, "价格上穿ema200", _initialize=_initialize
            )
        elif cross_type == CrossType.below:
            await self._send_wx(
                kline.opening_time, "价格下穿ema200", _initialize=_initialize
            )

        prev_ema60 = self.ema60[-2]

        cross_type = _resolve_cross(ema200, prev_ema60, ema60)
        if cross_type == CrossType.above:
            await self._send_wx(
                kline.opening_time, "ema60上穿ema200", _initialize=_initialize
            )
        elif cross_type == CrossType.below:
            await self._send_wx(
                kline.opening_time, "ema60下穿ema200", _initialize=_initialize
            )

    async def _send_wx(
        self,
        dt: datetime.datetime,
        message,
        level: str = "info",
        *,
        _initialize: bool,
    ):
        time_str = utils.format_datetime(dt)
        await utils.log_and_send_wx_message(
            f"[{self.symbol} - {self.timeframe}] {time_str}\n{message}",
            key=None if _initialize else self.wx_key,
            level=level,
        )


class EMAWatcherManger(WatcherManager):
    def create_exchange(self) -> Exchange:
        return settings.create_async_exchange_public("bitget")

    def create_watcher(
        self,
        kline_watcher: KLineWatcher,
        symbol: str,
        timeframe: str,
        wx_key: str | None = None,
        **kwargs,
    ):
        return EMAWatcher(
            kline_watcher=kline_watcher,
            symbol=symbol,
            timeframe=timeframe,
            wx_key=wx_key,
        )


async def entry():
    manager = EMAWatcherManger(
        symbols=[
            "PEPE/USDT:USDT",
        ],
        timeframes=["1m"],
        wx_key=None,
    )
    await manager.watch()


def main():
    asyncio.run(entry())


if __name__ == "__main__":
    main()
