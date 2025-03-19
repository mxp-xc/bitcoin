# -*- coding: utf-8 -*-
from typing import Any, TypedDict, Literal, TYPE_CHECKING

from trading.schema.base import KLine
from .schema import OrderInfo

if TYPE_CHECKING:
    from .base import Runner

_break_event_strategy = {}


class BreakEvenStrategyTypedDict(TypedDict):
    strategy: Literal["simple", "loss_price_base"]
    kwargs: dict[str, Any] | None


class BreakEvenStrategy(object):
    """保本策略"""


class SimpleBreakEvenStrategy(BreakEvenStrategy):
    pass


class LossPricePercentStrategy(BreakEvenStrategy):
    """基于止损的保本策略, 当止盈到达给定的止损的时候, 就会"""

    def __init__(
        self,
        percent: float,
        order_info: OrderInfo
    ):
        assert percent > 0
        self.order_info = order_info
        self.percent = percent
        self.profit_price = self._calc_min_profit_price()
        self.last_price = -1.0
        self._trigger = False

    def _calc_min_profit_price(self) -> float:
        delta_price = abs(self.order_info.price - self.order_info.preset_stop_loss_price) * self.percent
        if self.order_info.side == 'buy':
            return self.order_info.price + delta_price
        return self.order_info.price - delta_price

    async def get_profit_price(self, kline: KLine) -> float | None:
        """获取止盈价格, 返回None说明没有止盈"""
        if self._trigger:
            return self.profit_price
        if self.order_info.side == 'buy':
            self.last_price = max(self.last_price, kline.highest_price)
            if self.last_price >= self.profit_price:
                self._trigger = True
                return True
        else:
            self.last_price = min(self.last_price, kline.lowest_price)
            if self.last_price <= self.profit_price:
                self._trigger = True

        if self._trigger:
            return self.profit_price


class BreakEvenManager(object):
    def __init__(self, runner: "Runner"):
        self.runner = runner
