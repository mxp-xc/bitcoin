import asyncio
import datetime
from collections import OrderedDict
from typing import Callable

from ccxt.async_support import Exchange
from loguru import logger
from pydantic import BaseModel

from bitcoin.conf import settings
from bitcoin.trading import utils
from bitcoin.trading.schema.base import KLine, OrderBlock


class OrderBlockResult(BaseModel):
    order_blocks: list[OrderBlock]
    tested_order_blocks: list[OrderBlock]


class Options(BaseModel):
    min_fvg: int
    merge: bool


class KLineWrapper(BaseModel):
    kline: KLine
    closed: bool
    initialize: bool


class MaxWatchRetry(Exception):
    pass


class KLineWatcher(object):
    def __init__(self, exchange: Exchange, max_attempts: int = 3):
        self.exchange = exchange
        self.max_attempts = max_attempts
        self._retry_interval = 3

    async def async_iter(self, symbol: str, timeframe: str, *args, **kwargs):
        logger.info(f"initialize for `{symbol} - {timeframe}`")
        initialize = await self._watch_klines(
            symbol,
            timeframe,
            since=int((datetime.datetime.now() - datetime.timedelta(days=1)).timestamp() * 100),
            *args,
            **kwargs
        )
        *klines, prev_kline = initialize
        for kline in klines:
            yield KLineWrapper(kline=kline, closed=True, initialize=True)

        logger.info(f"start watch klines for `{symbol} - {timeframe}`")
        while True:
            klines = await self._watch_klines(symbol, timeframe, *args, **kwargs)
            for kline in klines:
                closed = kline.opening_time > prev_kline.opening_time
                yield KLineWrapper(kline=prev_kline, closed=closed, initialize=False)
                prev_kline = kline

    async def _watch_klines(self, symbol, timeframe, *args, **kwargs) -> list[KLine]:
        exception = None
        for i in range(self.max_attempts):
            try:
                return [
                    KLine.from_ccxt(ohlcv)
                    for ohlcv in await self.exchange.watch_ohlcv(
                        symbol, timeframe=timeframe,
                        *args, **kwargs
                    )
                ]
            except Exception as exc:
                exception = exc
                logger.exception(f"Failed to watch_ohlcv. : {exc!s} retry: {i}")
                await asyncio.sleep(self._retry_interval)
                continue
        if exception:
            raise MaxWatchRetry("Failed to watch ohlcv. max retry") from exception


class OrderBlockParser(object):
    granularity_map = {
        "1m": datetime.timedelta(minutes=1),
        "3m": datetime.timedelta(minutes=3),
        "5m": datetime.timedelta(minutes=5),
        "30m": datetime.timedelta(minutes=30),
        "1H": datetime.timedelta(hours=1),
        "4H": datetime.timedelta(hours=4),
    }

    def __init__(
        self,
        timeframe: str,
        min_fvg: int = 0,
        order_block_parser: Callable[[KLine, KLine, KLine], str] | None = None,
        merge: bool = False
    ):
        delta = self.granularity_map.get(timeframe)
        if delta is None:
            raise ValueError(f"unsupported granularity: `{timeframe}`")
        self._delta = delta
        self.options = Options(min_fvg=min_fvg, merge=merge)
        self.granularity = timeframe
        self.order_blocks: OrderedDict[str, OrderBlock] = OrderedDict()
        self.tested_order_blocks: dict[str, OrderBlock] = OrderedDict()
        self._order_block_parser = order_block_parser
        self._buffer: list[KLine] = []
        self._current_order_block: OrderBlock | None = None

    @property
    def next_kline_closing_datetime(self):
        """下一根k线的收盘时间"""
        last_kline = self.get_last_parse_kline()
        if last_kline:
            return last_kline.opening_time + self._delta - datetime.timedelta(seconds=1)
        return None

    def get_last_parse_kline(self) -> KLine | None:
        if self._buffer:
            return self._buffer[-1]
        return None

    def _debug_check(self, kline: KLine):
        try:
            last_kline = self._buffer[-1]
            assert last_kline.opening_time + self._delta == kline.opening_time
        except AssertionError:
            logger.error("Failed to fetch kline in debug check")
            raise

    def fetch(self, kline: KLine):
        """fetch一根k线, 返回新出现的订单块"""
        if self._buffer:
            self._debug_check(kline)
            last_kline = self._buffer[-1]
            # 之前的kline直接忽略
            if last_kline.opening_time + self._delta < kline.opening_time:
                return

        self._trigger_test(kline)

        self._buffer.append(kline)
        if len(self._buffer) < 3:
            return

        direction = self._parse(*self._buffer[-3:])
        if direction is None:
            # 新出现的kline和之前没有继续形成订单块, 只保留最后的两个, 继续计算
            self._current_order_block = None
            if len(self._buffer) > 2:
                self._buffer = [*self._buffer[-2:]]
            return

        # 当超过3个时, 说明已经出现了ob
        if self._current_order_block:
            self._current_order_block.klines.append(kline)
        else:
            order_block = self._current_order_block = OrderBlock(
                klines=list(self._buffer),
                side=direction
            )

            self.order_blocks[utils.format_datetime(order_block.start_datetime)] = order_block
        self._merge_order_block()

    def _trigger_test(self, kline: KLine):
        keys = []
        for key, ob in self.order_blocks.items():
            if self._test_order_block(ob, kline):
                ob.first_test_kline = kline
                keys.append(key)
        for key in keys:
            tested_order_block = self.order_blocks.pop(key)
            self.tested_order_blocks[key] = tested_order_block

    def _test_order_block(self, order_block: OrderBlock, kline: KLine) -> bool:  # noqa
        return order_block.test(kline)

    def _parse(self, k1: KLine, k2: KLine, k3: KLine):
        if self._order_block_parser:
            return self._order_block_parser(k1, k2, k3)
        return self._default_parse(k1, k2, k3)

    def _default_parse(self, k1: KLine, k2: KLine, k3: KLine):  # noqa
        if (
            k2.entity_highest_price >=
            k1.lowest_price >
            k3.highest_price >=
            k2.entity_lowest_price
        ):
            fvg = k1.lowest_price - k3.highest_price
            if fvg >= self.options.min_fvg:
                return "short"
            else:
                logger.info(f"k线{k1.opening_time}不满足最小fvg. {fvg} < {self.options.min_fvg}")

        if (
            k2.entity_lowest_price <=
            k1.highest_price <
            k3.lowest_price <=
            k2.entity_highest_price
        ):
            fvg = k3.lowest_price - k1.highest_price
            if fvg >= self.options.min_fvg:
                return "long"
            else:
                logger.info(f"k线{k1.opening_time}不满足最小fvg. {fvg} < {self.options.min_fvg}")
        return None

    def _merge_order_block(self):
        if not self.options.merge:
            return


async def main():
    obp = OrderBlockParser("30m")
    symbol = "BTC/USDT:USDT"
    timeframe = "30m"
    async with settings.create_async_exchange() as exchange:
        await exchange.watch_ohlcv(symbol)
        while True:
            ohlcv = await exchange.watch_ohlcv(symbol)
            print([
                KLine.from_ccxt(item)
                for item in ohlcv
            ])
        # klines = [
        #     KLine.from_ccxt(kl_)
        #     for kl_ in
        #     await exchange.fetch_ohlcv(
        #         "BTC/USDT:USDT",
        #         "30m",
        #     )
        # ]
        # for index, kl_ in enumerate(klines):
        #     obp.fetch(kl_)
        # for ob_ in obp.order_blocks.values():
        #     print(ob_.desc())


if __name__ == '__main__':
    asyncio.run(main())
