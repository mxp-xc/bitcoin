# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

from ccxt.base.types import OrderSide
from pydantic import BaseModel, ConfigDict

from trading.schema.base import OrderBlock, KLine

if TYPE_CHECKING:
    # for dev
    from ccxt.pro.bitget import bitget as Exchange  # noqa


class OrderInfo(BaseModel):
    side: OrderSide
    price: float | None = None
    amount: float | None = None
    preset_stop_surplus_price: float | None = None
    preset_stop_loss_price: float | None = None
    client_order_id: str | None = None


class PlaceOrderContext(BaseModel):
    order_blocks: list[OrderBlock]
    mutex_order_blocs: list[OrderBlock]
    current_kline: KLine

    model_config = ConfigDict(
        arbitrary_types_allowed=True
    )
