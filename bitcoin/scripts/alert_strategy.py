# -*- coding: utf-8 -*-
import asyncio
from enum import StrEnum

from loguru import logger
from pydantic import BaseModel
from talipp.indicators import RSI

from bitcoin.conf import settings
from bitcoin.trading.helper import KLineWatcher
from bitcoin.trading.schema.base import KLine
from bitcoin.utils import send_wx_message


class RSIStatus(StrEnum):
    normal: str = "normal"  # 正常区间
    overbought: str = "overbought"  # 超买
    oversold: str = "oversold"  # 超卖

    def desc_cn(self):
        if self == self.oversold:
            return "rsi超卖"
        if self == self.overbought:
            return "rsi超买"
        return "rsi正常"


class RSIProcessContext(BaseModel):
    prev_rsi_status: RSIStatus | None
    current_rsi_status: RSIStatus
    kline: KLine
    rsi: float


class RSIWatcher(object):
    def __init__(
        self,
        watcher: KLineWatcher,
        symbol: str,
        timeframe: str,
        wx_key: str | None = None,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self._wx_key = wx_key
        self._watcher = watcher

        self.rsi_indicator = RSI(14)
        self._low_rsi = 25
        self._high_rsi = 75

    async def watch(self):
        # 是否一直处于穿过状态
        prev_rsi_status: RSIStatus | None = None
        first_log = False
        async for wrapper in self._watcher.async_iter(
            self.symbol, self.timeframe
        ):
            if not wrapper.closed:
                if not first_log and not wrapper.initialize:
                    logger.info(
                        f"({self.symbol} - {self.timeframe} - {wrapper.kline.opening_time}) "
                        f"最新 {prev_rsi_status}"
                    )
                    first_log = True
                continue
            kline = wrapper.kline
            self.rsi_indicator.add(kline.closing_price)
            rsi = self.rsi_indicator[-1]
            if rsi is None:
                # 没有足够的k线计算rsi
                continue
            current_rsi_status = self._parse_rsi_status(rsi)
            context = RSIProcessContext(
                prev_rsi_status=prev_rsi_status,
                current_rsi_status=current_rsi_status,
                kline=kline,
                rsi=rsi,
            )

            if prev_rsi_status is None:
                prev_rsi_status = current_rsi_status
                await self._log_rsi_status(
                    f"初始化prev_rsi_status: {prev_rsi_status.desc_cn()}",
                    context,
                    send_wx=False,
                )
                continue
            if not wrapper.initialize:
                await self._process_rsi_status(context)
            prev_rsi_status = current_rsi_status

    async def _process_rsi_status(self, context: RSIProcessContext):
        prev_rsi_status = context.prev_rsi_status
        current_rsi_status = context.current_rsi_status
        assert prev_rsi_status, (
            f"prev_rsi_status: {prev_rsi_status} must not be None"
        )
        assert current_rsi_status, (
            f"prev_rsi_status: {current_rsi_status} must not be None"
        )

        if prev_rsi_status == current_rsi_status:  # 没有变化不处理
            return
        if prev_rsi_status == RSIStatus.normal:
            # 出现超买或者超卖
            await self._log_rsi_status(
                f"出现{current_rsi_status.desc_cn()}", context
            )
        else:
            # 超买或者超卖回归正常
            await self._log_rsi_status(
                f"{prev_rsi_status.desc_cn()}回归正常", context
            )

    async def _log_rsi_status(
        self,
        message,
        contex: RSIProcessContext,
        send_wx: bool = True,
        level: str = "info",
    ):
        final_message = f"""{self.symbol}({self.timeframe} - {contex.kline.opening_time}) {message}"""
        getattr(logger, level)(final_message)
        if send_wx and self._wx_key:
            assert self._wx_key
            await send_wx_message(final_message, key=self._wx_key)

    def _parse_rsi_status(self, rsi: float) -> RSIStatus:
        if rsi >= self._high_rsi:
            return RSIStatus.overbought
        if rsi <= self._low_rsi:
            return RSIStatus.oversold
        return RSIStatus.normal


class RSIWatcherManger(object):
    def __init__(
        self,
        symbols: list[str],
        timeframes: list[str],
        wx_key: str | None = None,
    ):
        self.symbols = symbols
        self.timeframes = timeframes
        self.wx_key = wx_key

    async def watch(self):
        rsi_watchers = []
        async with settings.create_async_exchange_public("bitget") as exchange:
            kline_watcher = KLineWatcher(exchange)
            await exchange.load_markets()
            for symbol in self.symbols:
                exchange.market(symbol)

                for timeframe in self.timeframes:
                    rsi_watcher = RSIWatcher(
                        watcher=kline_watcher,
                        symbol=symbol,
                        timeframe=timeframe,
                        wx_key=self.wx_key,
                    )
                    rsi_watchers.append(rsi_watcher)
        symbol_time_frames = (
            f"{watcher.symbol} - {watcher.timeframe}"
            for watcher in rsi_watchers
        )
        message = f"collect rsi watcher:\n{'\n'.join(symbol_time_frames)}"
        logger.info(message)
        if self.wx_key:
            await send_wx_message(message, key=self.wx_key)
        await asyncio.gather(*(watcher.watch() for watcher in rsi_watchers))


async def entry():
    manager = RSIWatcherManger(
        symbols=[
            "PEPE/USDT:USDT",
            "DOGE/USDT:USDT",
            "ADA/USDT:USDT",
            "SOL/USDT:USDT",
        ],
        timeframes=["1m", "5m", "15m"],
    )
    await manager.watch()


def main():
    asyncio.run(entry())


if __name__ == "__main__":
    main()
