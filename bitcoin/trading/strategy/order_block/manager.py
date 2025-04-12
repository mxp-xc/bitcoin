# -*- coding: utf-8 -*-
import asyncio
from typing import TYPE_CHECKING

from ccxt.pro import Exchange
from loguru import logger

from .base import RunnerOption, CustomRunnerOptions, Runner

if TYPE_CHECKING:
    # for dev
    from ccxt.pro.bitget import bitget as Exchange  # noqa


class RunnerManager(object):
    def __init__(
        self,
        options: list[RunnerOption],
        exchange: Exchange,
        product_type: str
    ):
        assert len({option.symbol for option in options}) == len(options)
        self.product_type = product_type
        self.exchange = exchange
        self.options = options

    def _build_runner(self, option: RunnerOption) -> Runner:
        if isinstance(option, CustomRunnerOptions):
            runner_class = option.runner_class
        else:
            runner_class = Runner
        return runner_class(
            symbol=option.symbol,
            product_type=self.product_type,
            exchange=self.exchange,
            timeframe=option.timeframe,
            position_strategy=option.position_strategy,
            first_min_fvg_percent=option.first_min_fvg_percent,
            min_fvg_percent=option.min_fvg_percent,
            break_even_strategy=option.break_even_strategy,
            min_order_block_kline_undulate_percent=option.min_order_block_kline_undulate_percent,
            max_order_block_kline_undulate_percent=option.max_order_block_kline_undulate_percent,
            **(option.init_kwargs or {})
        )

    async def _get_account_info(self):
        logger.info(f"获取合约账号信息({self.product_type})")
        balances = await self.exchange.fetch_balance({
            "type": "swap",
            "productType": self.product_type
        })
        if self.product_type.startswith("S"):
            balance = balances["SUSDT"]
        else:
            balance = balances["USDT"]
        margin_coin = balances["info"][0]["marginCoin"]
        logger.info(
            f"当前账号可用({margin_coin}): {balance['free']}, "
            f"已用: {balance['used']}, "
            f"总计: {balance['total']}, "
        )
        return balance

    async def run(self):
        await self._get_account_info()
        runners = [self._build_runner(option) for option in self.options]
        await asyncio.gather(*(runner.run() for runner in runners))
