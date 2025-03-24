# -*- coding: utf-8 -*-
import asyncio
from typing import TYPE_CHECKING

from ccxt.base.types import Position
from loguru import logger
from pydantic import BaseModel, ConfigDict

from trading.schema.base import KLine
from .schema import PlaceOrderWrapper

if TYPE_CHECKING:
    from .base import Runner


class PositionListener(object):
    def __init__(self, runner: "Runner", order_wrapper: PlaceOrderWrapper, position: Position):
        self.runner = runner
        self.order_wrapper = order_wrapper
        self.position = position

    async def on_open(self):
        logger.info("on open")

    async def on_close(self):
        logger.info("on close")


class PositionWrapper(BaseModel):
    order_wrapper: PlaceOrderWrapper
    listeners: list[PositionListener] | None = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True
    )


class KLinePositionListener(PositionListener):
    def __init__(self, *args, timeframe: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.timeframe = timeframe
        self._stopping = False

    async def on_open(self):
        logger.info(f"position listener start {self}")
        asyncio.create_task(self._listen_klines())

    async def on_close(self):
        logger.info(f"position listener close {self}")
        self._stopping = True

    async def _listen_klines(self):
        logger.info(f"{self.__class__.__qualname__} start watch ohlcv. {self.timeframe = }")
        # init
        await self.runner.exchange.watch_ohlcv(self.runner.symbol, self.timeframe)
        while not self._stopping:
            ohlcv_list = await self.runner.exchange.watch_ohlcv(
                self.runner.symbol,
                self.timeframe or self.runner.timeframe
            )
            klines = [KLine.from_ccxt(ohlcv) for ohlcv in ohlcv_list]
            await self._on_kline(klines)

    async def _on_kline(self, klines: list[KLine]):
        self._stopping = True
