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


support_exchanges = (
    "Binance", "OKX", "Bybit", "Bitmex"
)


class LargeOrderWatcher(object):
    def __init__(self, symbol: str, tick: int, threshold: int, type_: int = 1):
        self.obm = OrderBookModel()
        self.exchanges = support_exchanges
        self.symbol = symbol
        self.tick = tick
        self.threshold = threshold
        self.type_ = type_
        self._lock = asyncio.Lock()

    @cached_property
    def session(self):
        return aiohttp.ClientSession()

    async def alert(self):
        logger.info("start alert")
        await asyncio.sleep(5)

        logger.info("initialize")
        prev_bids, prev_asks = await self.calc()
        for price, volume in sorted(prev_asks.items(), key=lambda i: i[0]):
            if volume >= self.threshold:
                logger.info(f"{price} 存在大额空单 {volume}")
        for price, volume in sorted(prev_bids.items(), key=lambda i: i[0], reverse=True):
            if volume >= self.threshold:
                logger.info(f"{price} 存在大额多单 {volume}")
        await asyncio.sleep(1)
        logger.info("start aware change")

        while True:
            new_bids, new_asks = await self.calc()
            # 比较新的变化
            big_volumes_add, big_volumes_sub = [], []
            for price, new_volume in sorted(new_bids.items(), key=lambda i: i[0], reverse=True):
                old_volume = prev_bids.get(price, 0)
                # 新的挂单小于阈值, 并且旧的挂单大于阈值
                if new_volume < self.threshold < old_volume:
                    big_volumes_sub.append(f"{price} 大额买单变动(减少) {old_volume} -> {new_volume}")
                # 新的挂单大于阈值, 旧的挂单小于阈值
                elif old_volume < self.threshold < new_volume:
                    big_volumes_add.append(f"{price} 大额买单变动(新增) {old_volume} -> {new_volume}")

            if big_volumes_sub or big_volumes_add:
                now = datetime.datetime.now()
                logger.info(f"============  {utils.format_datetime(now)}  =============")
                for message in itertools.chain(big_volumes_add, big_volumes_sub):
                    logger.info(message)

            big_volumes_add, big_volumes_sub = [], []
            for price, new_volume in sorted(new_asks.items(), key=lambda i: i[0]):
                old_volume = prev_asks.get(price, 0)
                # 新的挂单小于阈值, 并且旧的挂单大于阈值
                if new_volume < self.threshold < old_volume:
                    big_volumes_sub.append(f"{price} 大额空单变动(减少) {old_volume} -> {new_volume}")
                # 新的挂单大于阈值, 旧的挂单小于阈值
                elif old_volume < self.threshold < new_volume:
                    big_volumes_add.append(f"{price} 大额多单变动(新增) {old_volume} -> {new_volume}")

            if big_volumes_sub or big_volumes_add:
                now = datetime.datetime.now()
                logger.info(f"============  {utils.format_datetime(now)}  =============")
                for message in itertools.chain(big_volumes_add, big_volumes_sub):
                    logger.info(message)

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
        async with self.session.ws_connect(
            url="wss://wss.coinglass.com/v2/ws",
            verify_ssl=False
        ) as ws:
            logger.info("subscribe")
            await self.subscribe(ws)
            asyncio.create_task(self.alert())

            logger.info(f"watch order book. exchanges: {self.exchanges}")
            async for message in ws:
                raw_data = gzip.decompress(message.data)
                item = orjson.loads(raw_data)
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


async def main():
    watcher = LargeOrderWatcher(
        symbol="BTC:USDT",  # 使用:分割, 目前coinglass只支持BTC:USDT和ETH:USDT
        tick=100,  # 统计的挂单个数的价格范围. 10为即每次统计10刀范围内的挂单. 目前coinglass只支持10, 50, 100三个数字
        threshold=500,  # 超过500个币种挂单就输出日志
    )
    await watcher.watch()


if __name__ == '__main__':
    asyncio.run(main())
