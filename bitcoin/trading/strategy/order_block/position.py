# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from bitcoin.trading import utils
from bitcoin.trading.exceptions import StopTradingException

from .schema import OrderInfo

if TYPE_CHECKING:
    # for dev
    from ccxt.pro.bitget import bitget as Exchange  # noqa
    from .base import Runner

_position_strategy = {}


class PositionStrategy(object):
    """仓位策略"""

    def __init__(self, leverage: int | None = None):
        if leverage:
            assert leverage > 0
        self.leverage = leverage

    def wrap_leverage(self, value: float):
        if self.leverage is None:
            return value
        return value * self.leverage

    async def get_amount(
        self, order_info: OrderInfo, runner: "Runner"
    ) -> float:
        raise NotImplementedError


class SimplePositionStrategy(PositionStrategy):
    def __init__(self, usdt: float, **kwargs):
        super().__init__(**kwargs)
        self.usdt = usdt

    async def get_amount(self, order_info: OrderInfo, runner: "Runner"):
        if not order_info.price:
            raise StopTradingException("没有入场价格")
        return self.usdt / order_info.price


_position_strategy["simple"] = SimplePositionStrategy


class ElasticityPositionStrategy(PositionStrategy):
    def __init__(self, base_total_usdt: float, base_usdt: float, **kwargs):
        assert 0 < base_usdt < base_total_usdt
        self.base_total_usdt = base_total_usdt
        self.base_usdt = base_usdt
        super().__init__(**kwargs)

    def _get_interval(self, x):
        x += 1
        n = 0
        while x > self.base_total_usdt * (2**n):
            n += 1
        return 2**n // 2

    async def _get_base_amount(self, runner: "Runner") -> float:
        balances = await runner.exchange.fetch_balance(
            {"type": "swap", "productType": runner.product_type}
        )
        if runner.product_type.startswith("S"):
            balance = balances["SUSDT"]
        else:
            balance = balances["USDT"]
        total_usdt = balance["total"]
        if self.base_total_usdt > total_usdt:
            raise StopTradingException(
                f"当前账号余额: {total_usdt}少于配置的仓位最小金额: {self.base_total_usdt}"
            )
        interval = self._get_interval(total_usdt)
        return self.base_usdt * interval

    async def get_amount(
        self, order_info: OrderInfo, runner: "Runner"
    ) -> float:
        if not order_info.price or not order_info.preset_stop_loss_price:
            raise StopTradingException("弹性仓位需要存在入场价格和止损价格")
        # 作为亏损的金额
        base_amount = await self._get_base_amount(runner)
        # 计算亏损比例
        percent = abs(
            utils.get_undulate_percent(
                order_info.price, order_info.preset_stop_loss_price
            )
        )
        # 实际需要投入的价格
        amount = base_amount / percent
        return amount / order_info.price


_position_strategy["elasticity"] = ElasticityPositionStrategy


class PositionStrategyTypedDict(TypedDict):
    strategy: Literal["simple", "elasticity"] | None
    kwargs: dict[str, Any] | None


def create_position_strategy(
    strategy: str | None, **kwargs
) -> PositionStrategy:
    if strategy is None:
        strategy = "simple"
    strategy_class = _position_strategy.get(strategy)
    if strategy_class is None:
        raise ValueError(f"{strategy}仓位策略")
    return strategy_class(**kwargs)
