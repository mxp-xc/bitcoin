# -*- coding: utf-8 -*-
import asyncio
import datetime

from conf import settings
from trading.strategy.order_block.base import CustomRunnerOptions, RunnerManager
from trading.strategy.order_block.btc import BTCRunner
from trading.strategy.order_block.eth import ETH5MRunner


async def main():
    async with settings.create_async_exchange() as exchange:
        options = [
            CustomRunnerOptions(
                symbol="BTC/USDT:USDT",
                timeframe="30m",
                position_strategy={
                    'strategy': 'simple',
                    'kwargs': {
                        'usdt': 1
                    }
                },
                runner_class=BTCRunner
            ),
            CustomRunnerOptions(
                symbol="ETH/USDT:USDT",
                timeframe="5m",
                position_strategy={
                    'strategy': 'simple',
                    'kwargs': {
                        'usdt': 1
                    }
                },
                runner_class=ETH5MRunner,
                init_kwargs={
                    "effective_start_time": datetime.timedelta(minutes=50),
                    "effective_end_time": datetime.timedelta(hours=2, minutes=40),
                    "order_block_kline_undulate_percent": 0.2,  # 订单块方向的振幅
                    "volume_percent_threshold": 1.9,  # 成交量比例
                    "profit_and_loss_ratio": 1.47,  # 盈亏比
                }
            ),
        ]
        rm = RunnerManager(options, exchange, "USDT-FUTURES")
        await rm.run()


if __name__ == '__main__':
    asyncio.run(main())
