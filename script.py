# -*- coding: utf-8 -*-
import asyncio
import datetime  # noqa

from conf import settings
from trading.strategy.order_block.base import CustomRunnerOptions, RunnerManager, RunnerOption
from trading.strategy.order_block.btc import BTCRunner, BTCRunner2  # noqa
from trading.strategy.order_block.eth import ETH5MRunner  # noqa


def _test():
    return RunnerOption(
        symbol="BTC/USDT:USDT",  # 交易对
        timeframe="30m",  # 时间框架
        position_strategy={  # 仓位策略,
            'strategy': 'simple',  # 简单策略
            'kwargs': {
                'usdt': 5  # 固定5u, 会计算杠杆
            }
        },
        min_fvg_percent=0.1,  # 第一个fvg需要满足的最小值比例. 默认为0
        min_order_block_kline_undulate_percent=0.2,  # 要做的最小的订单块振幅.
        max_order_block_kline_undulate_percent=1000,  # 要做的最大订单块振幅
    )


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
                runner_class=BTCRunner,
                min_fvg_percent=0.1,
                min_order_block_kline_undulate_percent=0.2,  # 最小振幅
                max_order_block_kline_undulate_percent=1.5,  # 最大振幅
                init_kwargs={
                    "middle_entry_undulate": 0.7,  # 中位入场的最低振幅
                }
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
                min_order_block_kline_undulate_percent=0.2,  # 入场需要满足的最小订单块方向的振幅
                init_kwargs={
                    "effective_start_time": datetime.timedelta(minutes=50),
                    "effective_end_time": datetime.timedelta(hours=2, minutes=40),
                    "volume_percent_threshold": 1.9,  # 成交量比例
                    "profit_and_loss_ratio": 1.47,  # 盈亏比
                }
            ),
        ]
        rm = RunnerManager(options, exchange, "USDT-FUTURES")
        await rm.run()


if __name__ == '__main__':
    asyncio.run(main())
