# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

from bitcoin.trading import utils
from bitcoin.trading.schema.base import OrderBlock

from ..base import EntryRunner, OrderInfo, PlaceOrderContext

if TYPE_CHECKING:
    # for dev
    from ccxt.pro.bitget import bitget as Exchange  # noqa


class BTCRunner(EntryRunner):
    async def _post_process_order_info(
        self,
        order_block: OrderBlock,
        context: PlaceOrderContext,
        order_info: OrderInfo,
    ) -> OrderInfo:
        """
        1. 无论工作日或者周末, 0.2%以下的订单块或者fvg < 0.1的不做
        2. 工作日0.4~0.7的, 边缘入场, 0.7~1.5中轨入场. 止损边缘. 止盈为止损的1.5倍.  大于1.5不做
        3. 周末, 0.4~0.7, 0.7~1.5入场规则和工作日一样. 止损多带0.2, 下单金额为止损总仓位5%的金额. 盈亏1:1
        4. 工作日, 当fvg < 0.05%视为不存在fvg
        """
        price = order_info.price
        preset_stop_loss_price = order_info.preset_stop_loss_price
        if order_block.side == "long":
            # 做多止损下影线
            if utils.is_workday():
                # 工作日盈亏比1:1.5
                preset_stop_surplus_price = price + 1.5 * (
                    price - preset_stop_loss_price
                )
            else:
                # 周末需要多带0.2的止损
                preset_stop_loss_price -= 0.002 * price
                # 盈亏比1:1
                preset_stop_surplus_price = price + (
                    price - preset_stop_loss_price
                )
        else:
            # 做空止损上影线
            if utils.is_workday():
                # 非周末盈亏比1:1.5
                preset_stop_surplus_price = price - 1.5 * (
                    preset_stop_loss_price - price
                )
            else:
                # 周末需要多带0.2的止损
                preset_stop_loss_price += 0.002 * price
                # 盈亏比1:1
                preset_stop_surplus_price = price - (
                    preset_stop_loss_price - price
                )

        order_info.preset_stop_surplus_price = preset_stop_surplus_price
        order_info.preset_stop_loss_price = preset_stop_loss_price
        order_info = await super()._post_process_order_info(
            order_block, context, order_info
        )
        return order_info


class BTCRunner2(EntryRunner):
    async def _post_process_order_info(
        self,
        order_block: OrderBlock,
        context: PlaceOrderContext,
        order_info: OrderInfo,
    ) -> OrderInfo:
        # 止损多带0.1个点
        loss_price = order_info.price * 0.001

        if order_block.side == "long":
            order_info.preset_stop_loss_price -= loss_price

            # 固定止盈工作日1个点, 周末0.5个点
            if utils.is_workday():
                # 工作日1个点
                order_info.preset_stop_surplus_price = (
                    order_info.price + 0.01 * order_info.price
                )
            else:
                # 周末0.5个点
                order_info.preset_stop_surplus_price = (
                    order_info.price + 0.005 * order_info.price
                )

        else:
            order_info.preset_stop_loss_price += loss_price
            # 固定止盈工作日1个点, 周末0.5个点
            if utils.is_workday():
                # 工作日1个点
                order_info.preset_stop_surplus_price = (
                    order_info.price - 0.01 * order_info.price
                )
            else:
                # 周末0.5个点
                order_info.preset_stop_surplus_price = (
                    order_info.price - 0.005 * order_info.price
                )

        order_info = await super()._post_process_order_info(
            order_block, context, order_info
        )
        return order_info
