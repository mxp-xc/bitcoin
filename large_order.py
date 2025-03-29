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
    def __init__(self, symbol: str, tick: int, threshold: int, type_: int = 1):
        self.obm = OrderBookModel()
        self.exchanges = swap_support_exchanges if type_ == 1 else spot_support_exchanges
        self.symbol = symbol
        self.tick = tick
        self.threshold = threshold
        self.type_ = type_
        self._lock = asyncio.Lock()
        self._stopping = False

    @cached_property
    def session(self):
        return aiohttp.ClientSession()

    async def alert(self):
        logger.info("start alert")
        await asyncio.sleep(5)

        logger.info("initialize")
        messages = []
        prev_bids, prev_asks = await self.calc()
        for price, volume in sorted(prev_asks.items(), key=lambda i: i[0]):
            if volume >= self.threshold:
                messages.append(f'{price} 存在大额<font color="warning">空单</font> {volume}')
        for price, volume in sorted(prev_bids.items(), key=lambda i: i[0], reverse=True):
            if volume >= self.threshold:
                messages.append(f'{price} 存在大额<font color="info">多单</font> {volume}')
        await self._log_and_send_wx_message(",\n".join(messages))
        await asyncio.sleep(1)
        logger.info("start aware change")

        while not self._stopping:
            new_bids, new_asks = await self.calc()
            # 比较新的变化
            big_volumes_add, big_volumes_sub = [], []
            for price, new_volume in sorted(new_bids.items(), key=lambda i: i[0], reverse=True):
                old_volume = prev_bids.get(price, 0)
                # 新的挂单小于阈值, 并且旧的挂单大于阈值
                if new_volume < self.threshold < old_volume:
                    big_volumes_sub.append(
                        f'{price} 大额<font color="info">多单</font>变动(减少) {old_volume} -> {new_volume}')
                # 新的挂单大于阈值, 旧的挂单小于阈值
                elif old_volume < self.threshold < new_volume:
                    big_volumes_add.append(
                        f'{price} 大额<font color="info">多单</font>变动(新增) {old_volume} -> {new_volume}')

            big_volumes_add, big_volumes_sub = [], []
            for price, new_volume in sorted(new_asks.items(), key=lambda i: i[0]):
                old_volume = prev_asks.get(price, 0)
                # 新的挂单小于阈值, 并且旧的挂单大于阈值
                if new_volume < self.threshold < old_volume:
                    big_volumes_sub.append(
                        f'{price} 大额<font color="warning">空单</font>变动(减少) {old_volume} -> {new_volume}')
                # 新的挂单大于阈值, 旧的挂单小于阈值
                elif old_volume < self.threshold < new_volume:
                    big_volumes_add.append(
                        f'{price} 大额<font color="warning">空单</font>变动(新增) {old_volume} -> {new_volume}')

            if big_volumes_sub or big_volumes_add:
                now = datetime.datetime.now()
                await self._log_and_send_wx_message(f"""
{utils.format_datetime(now)}
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
        tp = "合约" if self.type_ == 1 else "现货"
        await self._log_and_send_wx_message(
            f"开始监听{self.symbol}({tp})大额订单, 交易商{self.exchanges}, {self.tick = }, 阈值: {self.threshold}"
        )
        try:
            async with self.session.ws_connect(
                url="wss://wss.coinglass.com/v2/ws",
                verify_ssl=False
            ) as ws:
                logger.info("subscribe")
                await self.subscribe(ws)
                asyncio.create_task(self.alert())

                logger.info(f"watch order book. exchanges: {self.exchanges}")
                async for message in ws:
                    if self._stopping:
                        return
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
        except Exception as exc:
            await self._log_and_send_wx_message(f"Failed to watch large order: {exc!s}", level="exceptio")
        finally:
            self._stopping = True
            await self._log_and_send_wx_message("large order watcher bot stop")

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
        await utils.send_wx_message(message)


async def main():
    assert settings.wx_bot_key
    watcher = LargeOrderWatcher(
        symbol="BTC:USDT",  # 使用:分割, 目前coinglass只支持BTC:USDT和ETH:USDT
        tick=10,  # 统计的挂单个数的价格范围. 10为即每次统计10刀范围内的挂单. 目前coinglass只支持10, 50, 100三个数字
        threshold=300,  # 超过500个币种挂单就输出日志
        type_=1  # 1是合约, 0是现货
    )
    await watcher.watch()


if __name__ == '__main__':
    asyncio.run(main())
