# -*- coding: utf-8 -*-
import datetime
from typing import TYPE_CHECKING

from loguru import logger

from trading.schema.base import OrderBlock
from .base import Runner, PlaceOrderContext, OrderInfo

if TYPE_CHECKING:
    # for dev
    from ccxt.pro.bitget import bitget as Exchange  # noqa


class BTCRunner(Runner):
    @staticmethod
    def is_workday():
        """是否是工作日"""
        # return False
        return datetime.datetime.now().isoweekday() <= 5

    async def _choice_order_block(self, context: PlaceOrderContext) -> OrderBlock | None:
        if not context.order_blocks:
            return None
        for order_block in context.order_blocks:
            klines = order_block.klines
            k1 = order_block.order_block_kline
            if order_block.side == 'long':
                # 多单, 除下影线
                undulate = (k1.delta_price / k1.lowest_price) * 100
            else:
                # 多单, 除上影线
                undulate = (k1.delta_price / k1.highest_price) * 100
            if undulate <= 0.2 or undulate >= 1.5:
                logger.info(f"[振幅: reject] {undulate} <= 0.2 or {undulate} >= 1.5. {k1}")
                continue

            k2 = klines[2]
            if order_block.side == 'long':
                fvg = k2.lowest_price - k1.highest_price
            else:
                fvg = k1.lowest_price - k2.highest_price
            percent = (fvg / k1.highest_price) * 100
            # fvg小于0.1的略过
            if percent < 0.1:
                logger.info(f"[fvg: reject] {percent} < 0.1. {k1}")
                continue
            return order_block
        return None

    async def _resolve_order_info(
        self,
        order_block: OrderBlock,
        context: PlaceOrderContext
    ) -> OrderInfo:
        """
        1. 无论工作日或者周末, 0.2%以下的订单块或者fvg < 0.1的不做
        2. 工作日0.4~0.7的, 边缘入场, 0.7~1.5中轨入场. 止损边缘. 止盈为止损的1.5倍.  大于1.5不做
        3. 周末, 0.4~0.7, 0.7~1.5入场规则和工作日一样. 止损多带0.2, 下单金额为止损总仓位5%的金额. 盈亏1:1
        4. 工作日, 当fvg < 0.05%视为不存在fvg
        """
        kline = order_block.order_block_kline
        if order_block.side == 'long':
            # 多单, 除下影线
            undulate = (kline.delta_price / kline.lowest_price) * 100
        else:
            # 空单, 除上影线
            undulate = (kline.delta_price / kline.highest_price) * 100
        # assert 0.4 < undulate < 1.5
        if undulate > 0.7:
            # 中轨
            price = kline.center_price
        elif order_block.side == 'long':
            # 多上影线
            price = kline.highest_price
        else:
            # 空上影线
            price = kline.lowest_price

        if order_block.side == 'long':
            # 做多止损下影线
            preset_stop_loss_price = kline.lowest_price
            if self.is_workday():
                # 工作日盈亏比1:1.5
                preset_stop_surplus_price = price + 1.5 * (price - preset_stop_loss_price)
            else:
                # 周末需要多带0.2的止损
                preset_stop_loss_price -= 0.002 * price
                # 盈亏比1:1
                preset_stop_surplus_price = price + (price - preset_stop_loss_price)
        else:
            # 做空止损上影线
            preset_stop_loss_price = kline.highest_price
            if self.is_workday():
                # 非周末盈亏比1:1.5
                preset_stop_surplus_price = price - 1.5 * (preset_stop_loss_price - price)
            else:
                # 周末需要多带0.2的止损
                preset_stop_loss_price += 0.002 * price
                # 盈亏比1:1
                preset_stop_surplus_price = price - (preset_stop_loss_price - price)

        return OrderInfo(
            side='buy' if order_block.side == 'long' else 'sell',
            price=price,
            preset_stop_surplus_price=preset_stop_surplus_price,
            preset_stop_loss_price=preset_stop_loss_price
        )
