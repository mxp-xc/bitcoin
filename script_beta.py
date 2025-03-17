# -*- coding: utf-8 -*-
import asyncio
import datetime  # noqa

from conf import settings
from trading.strategy.order_block.base import CustomRunnerOptions
from trading.strategy.order_block.coin.btc import BTCRunner, BTCRunner2
from trading.strategy.order_block.coin.eth import ETH5MRunner
from trading.strategy.order_block.manager import RunnerManager

BTCRunner, BTCRunner2  # noqa


async def main():
    async with settings.create_async_exchange() as exchange:
        exchange.set_sandbox_mode(True)
        options = [
            CustomRunnerOptions(
                symbol="SBTCSUSDT",
                timeframe="30m",
                position_strategy={
                    'strategy': 'simple',
                    'kwargs': {
                        'usdt': 1,
                    }
                },
                runner_class=BTCRunner2,
                init_kwargs={
                    "middle_entry_undulate": 0.7,  # 中位入场的最低振幅
                }
            ),
            # CustomRunnerOptions(
            #     symbol="SETHSUSDT",
            #     timeframe="5m",
            #     position_strategy={
            #         'strategy': 'simple',
            #         'kwargs': {
            #             'usdt': 5,
            #         }
            #     },
            #     min_order_block_kline_undulate_percent=0.2,
            #     runner_class=ETH5MRunner,
            #     init_kwargs={
            #         "effective_start_time": datetime.timedelta(minutes=50),
            #         "effective_end_time": datetime.timedelta(hours=2, minutes=40),
            #         "volume_percent_threshold": 1.9,  # 成交量比例
            #         "profit_and_loss_ratio": 1.47,  # 盈亏比
            #     }
            # ),
        ]
        rm = RunnerManager(options, exchange, "SUSDT-FUTURES")
        await rm.run()


if __name__ == '__main__':
    asyncio.run(main())
