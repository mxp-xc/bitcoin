# -*- coding: utf-8 -*-
import asyncio

from loguru import logger

from bitcoin.conf import settings
from bitcoin.trading.schema.base import KLine


async def main():
    symbol = "BTC/USDT:USDT"
    timeframe = "30m"
    async with settings.create_async_exchange() as exchange:
        klines = [
            KLine.from_ccxt(ohlcv)
            for ohlcv in await exchange.watch_ohlcv(symbol, timeframe)
        ]
        prev_kline = klines[-1]  # 上一根k线, 刚开始为空

        while True:
            klines = [
                KLine.from_ccxt(ohlcv)
                for ohlcv in await exchange.watch_ohlcv(symbol, timeframe)
            ]
            for kline in klines:
                if kline.opening_time > prev_kline.opening_time:
                    closed_kline = prev_kline
                    logger.info(f"[{symbol} - {timeframe}] 收线了. {closed_kline}")
                prev_kline = kline


if __name__ == '__main__':
    asyncio.run(main())
