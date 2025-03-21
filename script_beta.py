# -*- coding: utf-8 -*-
import asyncio
import datetime  # noqa

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
                symbol="SBTCSUSDT",
                timeframe="30m",
                position_strategy={
                    'strategy': 'elasticity',
                    'kwargs': {
                        'base_total_usdt': 2000,
                        'base_usdt': 10
                    }
                },
                min_fvg_percent=0.1,
                runner_class=BTCRunner,
                init_kwargs={
                    "middle_entry_undulate": 0.7,  # 中位入场的最低振幅
                }
            ),
            CustomRunnerOptions(
                symbol="SETHSUSDT",
                timeframe="5m",
                position_strategy={
                    'strategy': 'elasticity',
                    'kwargs': {
                        'base_total_usdt': 2000,
                        'base_usdt': 10
                    }
                },
                min_order_block_kline_undulate_percent=0.2,
                runner_class=ETH5MRunner,
                init_kwargs={
                    "effective_start_time": datetime.timedelta(minutes=50),
                    "effective_end_time": datetime.timedelta(hours=2, minutes=40),
                    "volume_percent_threshold": 1.9,  # 成交量比例
                    "profit_and_loss_ratio": 1.47,  # 盈亏比
                }
            ),
        ]
        rm = RunnerManager(options, exchange, "SUSDT-FUTURES")
        await rm.run()


if __name__ == '__main__':
    asyncio.run(main())
