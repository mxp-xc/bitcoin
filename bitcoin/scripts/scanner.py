# -*- coding: utf-8 -*-
import asyncio
import datetime  # noqa
from typing import TYPE_CHECKING

from ccxt.async_support import Exchange
from ccxt.base.types import MarketInterface
from pydantic import BaseModel

from bitcoin.conf import settings
from bitcoin.entanglement_theory.parser import PenParser
from bitcoin.entanglement_theory.schema import FractalType, Pen
from bitcoin.trading.schema.base import KLine

if TYPE_CHECKING:
    from ccxt.pro.binance import binance as Exchange  # noqa


class LastPenResult(BaseModel):
    symbol: str
    pens: list[Pen]


# 1分钟, 5分钟, 30分钟, 4小时, 日线, 周


class Scanner(object):
    def __init__(self, exchange: Exchange, symbol: str):
        self.exchange = exchange
        self.symbol = symbol

    async def scan(self):
        klines = await self._get_klines()
        pen_parser = PenParser()
        pen_parser.parse(klines)
        pen_parser.print_pen()

        # if not pen_parser.pens:
        #     logger.info(f"{self.symbol} 没有出现笔")
        #     return
        # last_pen = pen_parser.pens[-1]
        # fractal_kline = last_pen.end.fractal_kline
        # if last_pen.end.type == FractalType.top:
        #     logger.info(f"{self.symbol} 收顶分型, 不处理")
        # else:
        #     logger.info(
        #         f"{self.symbol} 在{fractal_kline.opening_time}收低分型"
        #     )

    async def _get_klines(self) -> list[KLine]:
        # with open("./data.json", encoding="utf-8") as fp:
        #     value = json.load(fp)
        #     return [KLine(**k) for k in value]
        ohlcv_list = await self.exchange.fetch_ohlcv(
            symbol=self.symbol,
            timeframe="1w",
        )
        klines = [KLine.from_ccxt(ohlcv) for ohlcv in ohlcv_list]
        # start_datetime = datetime.datetime.strptime(
        #     "2024-10-28 08:00", "%Y-%m-%d %H:%M"
        # )
        # klines = list(
        #     filter(lambda k: k.opening_time >= start_datetime, klines)
        # )
        # with open("./data.json", "w", encoding="utf-8") as fp:
        #     value = [kline.model_dump(mode="json") for kline in klines]
        #     json.dump(value, fp)

        return klines


class Runner(object):
    def __init__(
        self,
        exchange: Exchange,
        timeframe: str,
        concurrent: int,
    ):
        self.exchange = exchange
        self.timeframe = timeframe
        self._semaphore = asyncio.Semaphore(concurrent)

    async def run(self):
        await self.exchange.load_markets()
        swap_coin: dict[str, MarketInterface] = {
            symbol: item for symbol, item in self.exchange.markets.items() if item["type"] == "swap" and item["active"]
        }
        items = list(swap_coin.items())
        # process = iter(tqdm.tqdm(items))
        async with asyncio.TaskGroup() as group:
            for symbol, info in items:
                await self._semaphore.acquire()
                group.create_task(self._log_bottom_fractal(symbol, None))

    async def _log_bottom_fractal(self, symbol, process):
        pen_result = await self._get_pens(symbol, process)
        if not pen_result or len(pen_result.pens) <= 2:
            return

        last_pen = pen_result.pens[-1]
        if last_pen.end.type == FractalType.bottom:
            return
        prev_pen = pen_result.pens[-2]
        prev_top_fractal_kline = prev_pen.start.fractal_kline
        top_fractal_kline = last_pen.end.fractal_kline
        if top_fractal_kline.highest_price > prev_top_fractal_kline.highest_price:
            print(f"{symbol} 在{top_fractal_kline.opening_time}突破回踩")

    async def _get_klines(self, symbol: str) -> list[KLine]:
        ohlcv_list = None
        for _ in range(3):
            try:
                ohlcv_list = await self.exchange.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=self.timeframe,
                )
                break
            except Exception as exc:  # noqa
                await asyncio.sleep(1)
        if ohlcv_list is None:
            ohlcv_list = await self.exchange.fetch_ohlcv(
                symbol=symbol,
                timeframe=self.timeframe,
            )
        return [KLine.from_ccxt(ohlcv) for ohlcv in ohlcv_list]

    async def _get_pens(self, symbol, process) -> LastPenResult | None:
        try:
            klines = await self._get_klines(symbol)
            pen_parser = PenParser()
            pen_parser.parse(klines)
            return LastPenResult(pens=pen_parser.pens, symbol=symbol)
        finally:
            self._semaphore.release()
            if process:
                try:
                    next(process)
                except StopIteration:
                    pass


async def run_main():
    """创建一个只使用"""
    # 配置一个至少拥有行情权限的api账号
    async with settings.exchange.create_async_exchange_public("binance") as exchange:
        await Runner(exchange, "1d", concurrent=3).run()
        # await Scanner(exchange, "FET/USDT:USDT").scan()


def main():
    asyncio.run(run_main())


if __name__ == "__main__":
    main()
