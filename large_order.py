# -*- coding: utf-8 -*-
import asyncio
import datetime
import gzip
import itertools
from collections import defaultdict
from functools import cached_property
from typing import Literal

import aiohttp
import orjson
from loguru import logger
from pydantic import BaseModel, ConfigDict

from conf import settings
from trading import utils


class OrderBookData(BaseModel):
    action: Literal["snapshot", "update"]
    asks: list[list[float]]
    bids: list[list[float]]
    time: int


class OrderBook(BaseModel):
    channel: str
    data: OrderBookData
    params: dict


class ExchangeOrderWrapper(BaseModel):
    lock: asyncio.Lock = asyncio.Lock()
    exchange_volume_map: dict[str, float] = defaultdict(float)

    model_config = ConfigDict(
        arbitrary_types_allowed=True
    )


class OrderBookModel(BaseModel):
    asks: dict[float, ExchangeOrderWrapper] = defaultdict(ExchangeOrderWrapper)
    bids: dict[float, ExchangeOrderWrapper] = defaultdict(ExchangeOrderWrapper)


swap_support_exchanges = (
    "Binance", "OKX", "Bybit", "Bitmex"
)
spot_support_exchanges = (
    "Binance", "OKX"
)


class LargeOrderWatcher(object):
    def __init__(
        self,
        symbol: str,
        tick: int,
        thresholds: list[float],
        type_: int = 1,
        panorama_interval: float = 60 * 60,
        wx_key: str | None = None,
    ):
        self.obm = OrderBookModel()
        self.exchanges = swap_support_exchanges if type_ == 1 else spot_support_exchanges
        self.symbol = symbol
        self.tick = tick
        self.thresholds = sorted(thresholds)
        self.type_ = type_
        self.panorama_interval = panorama_interval
        self._wx_key = wx_key
        self._lock = asyncio.Lock()
        self._stopping = False
        self._alert_task = None
        self._alert_panorama_task = None

    @cached_property
    def session(self):
        return aiohttp.ClientSession()

    @property
    def lower_threshold(self) -> float:
        return self.thresholds[0]

    @property
    def type_desc(self) -> str:
        if self.type_ == 0:
            return "现货"
        return "合约"

    def _panorama_interval_desc(self) -> str:
        delta = datetime.timedelta(seconds=self.panorama_interval)
        dt = datetime.datetime.min + delta
        result = dt.strftime("%H小时%M分钟%S秒")
        return result

    def _find_threshold_index(self, value: float) -> int:
        for index, threshold in enumerate(self.thresholds, 0):
            if value < threshold:
                return index - 1
        return len(self.thresholds) - 1

    async def _alert_panorama(self):
        logger.info("_alert_panorama")
        messages = []
        prev_bids, prev_asks = await self.calc()
        for price, volume in sorted(prev_asks.items(), key=lambda i: i[0], reverse=True):
            if volume >= self.lower_threshold:
                messages.append(f'{price} 存在大额<font color="warning">空单</font> {volume}')
        for price, volume in sorted(prev_bids.items(), key=lambda i: i[0], reverse=True):
            if volume >= self.lower_threshold:
                messages.append(f'{price} 存在大额<font color="info">多单</font> {volume}')
        if messages:
            now = datetime.datetime.now()
            await self._log_and_send_wx_message(
                f"""{self.type_desc}全景图 {utils.format_datetime(now)}\n{"\n".join(messages)}"""
            )

    async def _alert_panorama_interval(self):
        await asyncio.sleep(self.panorama_interval)
        while not self._stopping:
            await self._alert_panorama()
            await asyncio.sleep(self.panorama_interval)

    async def alert(self):
        logger.info(f"{self.type_desc} start alert")
        await asyncio.sleep(5)
        await self._alert_panorama()
        if self._alert_panorama_task is None:
            self._alert_panorama_task = asyncio.create_task(self._alert_panorama_interval())
            await asyncio.sleep(.1)
        logger.info(f"({self.type_desc}) start aware change")

        prev_bids, prev_asks = await self.calc()
        while not self._stopping:
            new_bids, new_asks = await self.calc()
            # 比较新的变化
            big_volumes_add, big_volumes_sub = [], []
            for price, new_volume in sorted(new_bids.items(), key=lambda i: i[0], reverse=True):
                old_volume = prev_bids.get(price, 0)

                new_index = self._find_threshold_index(new_volume)
                old_index = self._find_threshold_index(old_volume)
                if new_index > old_index:
                    big_volumes_add.append(
                        f'{price} 大额<font color="info">多单</font>变动(新增) {old_volume} -> {new_volume}')
                elif new_index < old_index:
                    big_volumes_sub.append(
                        f'{price} 大额<font color="info">多单</font>变动(减少) {old_volume} -> {new_volume}')

            big_volumes_add, big_volumes_sub = [], []
            for price, new_volume in sorted(new_asks.items(), key=lambda i: i[0]):
                old_volume = prev_asks.get(price, 0)

                new_index = self._find_threshold_index(new_volume)
                old_index = self._find_threshold_index(old_volume)
                if new_index > old_index:
                    big_volumes_add.append(
                        f'{price} 大额<font color="warning">空单</font>变动(新增) {old_volume} -> {new_volume}')
                elif new_index < old_index:
                    big_volumes_sub.append(
                        f'{price} 大额<font color="warning">空单</font>变动(减少) {old_volume} -> {new_volume}')

            logger.info("loop 1")
            if big_volumes_sub or big_volumes_add:
                logger.info("=" * 100)
                now = datetime.datetime.now()
                await self._log_and_send_wx_message(f"""{utils.format_datetime(now)} ({self.type_desc})
{"\n".join(itertools.chain(big_volumes_add, big_volumes_sub))}
""")

            prev_bids, prev_asks = new_bids, new_asks
            await asyncio.sleep(1)

    async def calc(self) -> tuple[dict[float, float], dict[float, float]]:
        async with self._lock:
            q = (defaultdict(float), defaultdict(float))
            for index, d in enumerate((self.obm.bids, self.obm.asks)):
                item = q[index]
                for price, wrapper in d.items():
                    volume = sum(wrapper.exchange_volume_map.values())
                    item[price] = volume

        return q

    async def watch(self):
        await self._log_and_send_wx_message(
            f"开始监听{self.symbol}({self.type_desc})大额订单,"
            f"交易商{self.exchanges},"
            f"{self.tick = },"
            f"阈值: {self.thresholds},"
            f"全景图告警间隔: {self._panorama_interval_desc()}"
        )
        retry = 0
        try:
            while True:
                try:
                    await self.restart()
                except Exception as exc:
                    if retry > 3:
                        raise
                    retry += 1
                    message = f"({self.type_desc}) Failed to watch coinglass. reason: {exc!s}. retry: {retry}. wait 1 min"
                    logger.error(message)
                    await self._send_wx_message(message)
                    await asyncio.sleep(60)
                else:
                    retry = 0
        except Exception as exc:
            await self._log_and_send_wx_message(
                f"{self.type_desc} Failed to watch large order: {exc!s}",
                level="exception"
            )
        finally:
            self._stopping = True
            await self._log_and_send_wx_message(f"({self.type_desc}) large order watcher bot stop")

    async def restart(self):
        if self._alert_task is None:
            async def _start_alert():
                try:
                    await self.alert()
                except Exception:
                    logger.exception("Failed to run alert")
                    raise
                finally:
                    self._stopping = True

            self._alert_task = asyncio.create_task(_start_alert())

        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                url="wss://wss.coinglass.com/v2/ws",
                verify_ssl=False
            ) as ws:
                await self._watch(ws)
        logger.info(f"({self.type_desc}) restart client session")

    async def _watch(self, ws: aiohttp.ClientWebSocketResponse):
        logger.info(f"{self.type_desc} subscribe")
        await self.subscribe(ws)

        logger.info(f"{self.type_desc} watch order book. exchanges: {self.exchanges}")
        prev_datetime = datetime.datetime.now()
        interval = datetime.timedelta(minutes=1)
        async for message in ws:
            if self._stopping:
                return
            now = datetime.datetime.now()
            if now - prev_datetime >= interval:
                # 一分钟记录一次在运行中
                logger.info(f"{self.type_desc} listening")
                prev_datetime = now
            raw_data = gzip.decompress(message.data)
            item = orjson.loads(raw_data)
            if not item.get("data"):
                await self._log_and_send_wx_message(f"{self.type_desc} data not recv. wait 1 seconds")
                await asyncio.sleep(1)
                continue

            order_book = OrderBook(**item)
            exchange = order_book.params["key"].split(":", 1)[0]
            async with self._lock:
                for d, w in (
                    (order_book.data.bids, self.obm.bids),
                    (order_book.data.asks, self.obm.asks),
                ):
                    for price, volume in d:
                        wrapper = w[price]
                        wrapper.exchange_volume_map[exchange] = volume

    async def subscribe(self, ws: aiohttp.ClientWebSocketResponse):
        for exchange in self.exchanges:
            await ws.send_json(
                {
                    "method": "subscribe",
                    "params": [
                        {
                            f"key": f"{exchange}:{self.symbol}:{self.type_}",
                            "tick": str(self.tick),
                            "channel": "orderBookV2"
                        }
                    ]
                }
            )

    async def _log_and_send_wx_message(self, message, level='info'):  # noqa
        getattr(logger, level)(message)
        await self._send_wx_message(message)

    async def _send_wx_message(self, message: str):
        if self._wx_key:
            await utils.send_wx_message(message, key=self._wx_key)


async def main():
    swap_watcher = LargeOrderWatcher(
        symbol="BTC:USDT",
        tick=10,
        thresholds=list(itertools.chain([300], range(500, 1300, 100))),
        type_=1,
        wx_key=settings.btc_swap_wx_bot_key
    )
    # spot_watcher = LargeOrderWatcher(
    #     symbol="BTC:USDT",
    #     tick=10,
    #     thresholds=list(range(50, 500, 50)),
    #     type_=0,
    #     wx_key=settings.btc_spot_wx_bot_key
    # )
    await asyncio.gather(
        # spot_watcher.watch(),
        swap_watcher.watch()
    )


if __name__ == '__main__':
    asyncio.run(main())
