import asyncio
import datetime
from collections import OrderedDict

from ccxt.async_support import Exchange
from ccxt.base.types import PositionSide
from loguru import logger
from pydantic import BaseModel

from bitcoin import utils
from bitcoin.conf import settings
from bitcoin.trading.schema.base import KLine, OrderBlock


class FetchResult(BaseModel):
    order_block: OrderBlock | None = None
    tested_order_blocks: list[OrderBlock]
    update_order_block: OrderBlock | None = None


class OrderBlockResult(BaseModel):
    order_blocks: list[OrderBlock]
    tested_order_blocks: list[OrderBlock]


class Options(BaseModel):
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
            since=int(
                (
                    datetime.datetime.now() - datetime.timedelta(days=1)
                ).timestamp()
                * 100
            ),
            *args,
            **kwargs,
        )
        *klines, prev_kline = initialize
        for kline in klines:
            yield KLineWrapper(kline=kline, closed=True, initialize=True)

        logger.info(f"start watch klines for `{symbol} - {timeframe}`")
        while True:
            klines = await self._watch_klines(
                symbol, timeframe, *args, **kwargs
            )
            for kline in klines:
                closed = kline.opening_time > prev_kline.opening_time
                yield KLineWrapper(
                    kline=prev_kline, closed=closed, initialize=False
                )
                prev_kline = kline

    async def _watch_klines(
        self, symbol, timeframe, *args, **kwargs
    ) -> list[KLine] | None:
        exception = None
        for i in range(self.max_attempts):
            try:
                return [
                    KLine.from_ccxt(ohlcv)
                    for ohlcv in await self.exchange.watch_ohlcv(
                        symbol, timeframe=timeframe, *args, **kwargs
                    )
                ]
            except Exception as exc:
                exception = exc
                logger.exception(
                    f"Failed to watch_ohlcv. : {exc!s} retry: {i}"
                )
                await asyncio.sleep(self._retry_interval)
                continue
        if exception:
            raise MaxWatchRetry(
                "Failed to watch ohlcv. max retry"
            ) from exception
        return None


class _BaseOrderParser(object):
    _buffer: list[KLine]
    _delta: datetime.timedelta

    @property
    def next_kline_closing_datetime(self):
        """下一根k线的收盘时间"""
        last_kline = self.get_last_parse_kline()
        if last_kline:
            return (
                last_kline.opening_time
                + self._delta
                - datetime.timedelta(seconds=1)
            )
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

    def _is_kline_valid(self, kline: KLine):
        if self._buffer:
            self._debug_check(kline)
            last_kline = self._buffer[-1]
            # 之前的kline直接忽略
            if last_kline.opening_time + self._delta < kline.opening_time:
                return False
        return True

    def _parse(self, k1: KLine, k2: KLine, k3: KLine) -> PositionSide | None:  # noqa
        if (
            k2.entity_highest_price
            >= k1.lowest_price
            > k3.highest_price
            >= k2.entity_lowest_price
        ):
            return "short"

        if (
            k2.entity_lowest_price
            <= k1.highest_price
            < k3.lowest_price
            <= k2.entity_highest_price
        ):
            return "long"
        return None


class OrderBlockParser(_BaseOrderParser):
    def __init__(
        self,
        timeframe: str,
        merge: bool = False,
    ):
        self._delta = datetime.timedelta(
            seconds=Exchange.parse_timeframe(timeframe)
        )
        self.options = Options(merge=merge)
        self.order_blocks: OrderedDict[str, OrderBlock] = OrderedDict()
        self.tested_order_blocks: dict[str, OrderBlock] = OrderedDict()
        self._buffer: list[KLine] = []
        self._current_order_block: OrderBlock | None = None

    def fetch(self, kline: KLine) -> FetchResult | None:
        """fetch一根k线, 返回新出现的订单块"""
        if not self._is_kline_valid(kline):
            return None

        self._buffer.append(kline)
        if len(self._buffer) < 3:
            return None
        tested_order_blocks = self._trigger_test(kline)
        result = FetchResult(tested_order_blocks=tested_order_blocks)

        direction = self._parse(*self._buffer[-3:])
        if direction is None:
            # 新出现的kline和之前没有继续形成订单块, 只保留最后的两个, 继续计算
            self._current_order_block = None
            if len(self._buffer) > 2:
                self._buffer = [*self._buffer[-2:]]
            if tested_order_blocks:
                return result
            return None

        # 当超过3个时, 说明已经出现了ob
        if self._current_order_block:
            self._current_order_block.klines.append(kline)
            result.update_order_block = self._current_order_block
        else:
            order_block = self._current_order_block = OrderBlock(
                klines=list(self._buffer), side=direction
            )

            self.order_blocks[
                utils.format_datetime(order_block.start_datetime)
            ] = order_block
            result.order_block = order_block
        self._merge_order_block()
        return result

    def _trigger_test(self, kline: KLine) -> list[OrderBlock]:
        keys = []
        all_tested_order_blocks = []
        for key, ob in self.order_blocks.items():
            if self._test_order_block(ob, kline):
                ob.first_test_kline = kline
                keys.append(key)
        for key in keys:
            tested_order_block = self.order_blocks.pop(key)
            all_tested_order_blocks.append(tested_order_block)
            self.tested_order_blocks[key] = tested_order_block

        return all_tested_order_blocks

    def _test_order_block(self, order_block: OrderBlock, kline: KLine) -> bool:  # noqa
        return order_block.test(kline)

    def _merge_order_block(self):
        if not self.options.merge:
            return


async def main():
    async with settings.create_async_exchange() as exchange:
        klines = [
            KLine.from_ccxt(kl_)
            for kl_ in await exchange.fetch_ohlcv(
                "BTC/USDT:USDT", "30m", limit=1000
            )
        ]
        obp = OrderBlockParser("30m")
        for index, kl_ in enumerate(klines):
            result = obp.fetch(kl_)
            if not result:
                continue
            logger.info(f"======= {kl_.opening_time} =========")
            if result.order_block:
                logger.info(f"新增ob: {result.order_block.desc()}")
            if result.update_order_block:
                logger.info(f"ob延续: {result.update_order_block.desc()}")
            if result.tested_order_blocks:
                logger.info(
                    f"ob被测试: {[ob.desc() for ob in result.tested_order_blocks]}"
                )
            logger.info(f"======= {kl_.opening_time} =========\n")

        # for ob_ in obp.order_blocks.values():
        #     print(ob_.desc())


if __name__ == "__main__":
    asyncio.run(main())
