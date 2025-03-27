# -*- coding: utf-8 -*-
import asyncio
import datetime  # noqa

from loguru import logger

from conf import settings
from trading.schema.base import KLine, OrderBlock
from trading.strategy.order_block.base import CustomRunnerOptions
from trading.strategy.order_block.coin.btc import BTCRunner, BTCRunner2
from trading.strategy.order_block.coin.eth import ETH5MRunner
from trading.strategy.order_block.manager import RunnerManager

BTCRunner, BTCRunner2, ETH5MRunner  # noqa


class TestRunner(BTCRunner2):
    async def _get_klines(self, since: int | None = None, until: int | None = None) -> list[KLine]:
        fmt = '%Y-%m-%d %H:%M'
        start = datetime.datetime.strptime('2025-03-16 18:30', fmt)
        end = datetime.datetime.strptime('2025-03-17 16:00', fmt)
        return await super()._get_klines(int(start.timestamp() * 1000), int(end.timestamp() * 1000))

    async def _create_order(
        self,
        order_blocks: list[OrderBlock],
        mutex_order_blocs: list[OrderBlock],
        klines: list[KLine]
    ):
        return await super()._create_order(order_blocks, mutex_order_blocs, klines)


async def main():
    async with settings.create_async_exchange() as exchange:
        exchange.set_sandbox_mode(True)
        options = [
            CustomRunnerOptions(
                symbol="SBTC/SUSDT:SUSDT",
                timeframe="30m",
                position_strategy={
                    'strategy': 'elasticity',
                    'kwargs': {
                        'base_total_usdt': 1000,
                        'base_usdt': 25
                    }
                },
                break_even_strategy={
                    'strategy': 'loss_price_base',
                    'kwargs': {
                        'percent': 0.9
                    }
                },
                runner_class=BTCRunner,
                min_fvg_percent=0.1,
                min_order_block_kline_undulate_percent=0.2,  # 最小振幅
                max_order_block_kline_undulate_percent=1.5,  # 最大振幅
                init_kwargs={
                    "middle_entry_undulate": 0.7,  # 中位入场的最低振幅
                }
            )
        ]
        rm = RunnerManager(options, exchange, "SUSDT-FUTURES")
        try:
            await rm.run()
        except:  # noqa
            logger.exception("Failed to run script.py")


if __name__ == '__main__':
    asyncio.run(main())
