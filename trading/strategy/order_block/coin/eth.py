# -*- coding: utf-8 -*-
import datetime
from typing import TYPE_CHECKING

from trading.schema.base import OrderBlock
from ..base import Runner, PlaceOrderContext, OrderInfo, KLine

if TYPE_CHECKING:
    # for dev
    from ccxt.pro.bitget import bitget as Exchange  # noqa

KLine  # noqa


class ETH5MRunner(Runner):
    def __init__(
        self,
        effective_start_time: datetime.timedelta,
        effective_end_time: datetime.timedelta,
        volume_percent_threshold: float = 1.9,
        profit_and_loss_ratio: float = 1.47,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.profit_and_loss_ratio = profit_and_loss_ratio
        self.volume_percent_threshold = volume_percent_threshold
        self.effective_start_time = effective_start_time
        self.effective_end_time = effective_end_time

    # async def _get_klines(self, since: int | None = None, until: int | None = None) -> list[KLine]:
    #     start = datetime.datetime(year=2025, month=3, day=12, hour=1, minute=45)
    #     end = start.replace(hour=7, minute=40)
    #     return await super()._get_klines(int(start.timestamp() * 1000), int(end.timestamp() * 1000))

    async def _choice_order_block_extra(
        self,
        order_block: OrderBlock,
        context: PlaceOrderContext,
    ):
        k1, k2 = order_block.klines[0], order_block.klines[1]
        message = []
        elapsed = context.current_kline.opening_time - k1.opening_time
        if elapsed < self.effective_start_time or elapsed > self.effective_end_time:
            minus = elapsed.total_seconds() // 60
            message.append(f"[时间]. 出现到当前的时间: {minus}min不满足")
        volume_percent = k2.volume / k1.volume
        if volume_percent < self.volume_percent_threshold:
            message.append(f"[成交量比例] {volume_percent} < {self.volume_percent_threshold}.")

        return message

    async def _resolve_order_info(self, order_block: OrderBlock, context: PlaceOrderContext) -> OrderInfo:
        kline = order_block.order_block_kline
        if order_block.side == 'long':
            # 多单入场在上影线
            price = kline.highest_price
            # 止损在下影线
            preset_stop_loss_price = kline.lowest_price
            # 盈亏比1:1.47
            preset_stop_surplus_price = price + kline.delta_price * self.profit_and_loss_ratio
        else:
            # 空单入场在下影线
            price = kline.lowest_price
            # 止损在上影线
            preset_stop_loss_price = kline.highest_price
            # 盈亏比1:1.47
            preset_stop_surplus_price = price - kline.delta_price * self.profit_and_loss_ratio

        return OrderInfo(
            side="buy" if order_block.side == "long" else "sell",
            price=price,
            preset_stop_surplus_price=preset_stop_surplus_price,
            preset_stop_loss_price=preset_stop_loss_price
        )
