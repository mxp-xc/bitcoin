# -*- coding: utf-8 -*-
import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, AliasGenerator, PositiveInt, Field
from pydantic.alias_generators import to_camel, to_snake

import utils


class ToCamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=AliasGenerator(
            serialization_alias=to_snake,
            validation_alias=to_camel
        )
    )


class AccountInfo(ToCamelModel):
    """账户信息"""
    margin_coin: str
    available: float
    union_available: float


class Position(ToCamelModel):
    """仓位"""
    product_type: str  # 产品类型
    symbol: str  # 币种
    hold_side: Literal["long", "short"]  # "持仓方向(多, 空)
    margin_mode: Literal["crossed", "isolated"]  # 保证金模式: crossed(全仓), isolated(逐仓)
    margin_size: float  # 保证金
    leverage: PositiveInt  # 杠杆倍数
    pos_mode: Literal["one_way_mode", "hedge_mode"]  # 持仓模式(单向, 双向)
    open_price_avg: float  # 平均开仓价
    mark_price: float  # 标记价格
    unrealized_pl: float = Field(..., validation_alias="unrealizedPL")  # 未实现盈亏
    take_profit_id: str  # 止盈订单id
    stop_loss_id: str  # 止损订单id

    @property
    def identity(self):
        """仓位标识. 仓位没有id, 通过币种, 持仓方向, 产品类型决定"""
        return f"{self.symbol} - {self.hold_side} - {self.product_type}"

    def as_zh_str(self):
        if self.hold_side == "long":
            sign = -1
        else:
            sign = 1
        k = sign * (1 - (self.mark_price / self.open_price_avg)) * self.leverage * 100

        return (f"Position("
                f"币种={self.symbol}({self.leverage}x), "
                f"持仓方向={self.hold_side}, "
                f"保证金={round(self.margin_size, 4)}, "
                f"未实现盈亏={self.unrealized_pl}, "
                f"回报率={round(k, 2)}%"
                f")")

    __str__ = as_zh_str

    __repr__ = as_zh_str


class KLine(BaseModel):
    opening_time: datetime.datetime  # 开盘时间
    opening_price: float  # 开盘价格
    highest_price: float  # 最高价
    lowest_price: float  # 最低价
    closing_price: float  # 收盘价(当前K线未结束的及为最新价)
    volume: float  # 成交量

    model_config = ConfigDict(
        json_encoders={
            datetime.datetime: utils.format_datetime
        }
    )

    @classmethod
    def from_ccxt(cls, kline: list):
        return cls(
            opening_time=datetime.datetime.fromtimestamp(int(int(kline[0]) / 1000)),
            opening_price=kline[1],
            highest_price=kline[2],
            lowest_price=kline[3],
            closing_price=kline[4],
            volume=kline[5]
        )

    @property
    def entity_highest_price(self):
        return max(self.opening_price, self.closing_price)

    @property
    def entity_lowest_price(self):
        return min(self.opening_price, self.closing_price)

    @property
    def delta_price(self):
        return self.highest_price - self.lowest_price

    def as_str_zh(self):
        return (
            f"KLine("
            f"开盘时间={utils.format_datetime(self.opening_time)}, "
            f"开盘价格={self.opening_price}, "
            f"最高价格={self.highest_price}, "
            f"最低价格={self.lowest_price}, "
            f"收盘价格={self.closing_price}, "
            f"成交量={self.volume}"
            f")"
        )

    __str__ = as_str_zh

    __repr__ = as_str_zh


class BaseOrderBlock(BaseModel):
    side: str


class OrderBlock(BaseOrderBlock):
    klines: list[KLine]
    first_test_kline: KLine | None = None

    model_config = ConfigDict(
        json_encoders={
            datetime.datetime: utils.format_datetime
        }
    )

    @property
    def identity(self) -> str:
        return utils.format_datetime(self.start_datetime)

    @property
    def center_price(self):
        return (self.klines[0].lowest_price + self.klines[0].highest_price) / 2

    @property
    def start_datetime(self):
        return self.order_block_kline.opening_time

    @property
    def order_block_kline(self) -> KLine:
        return self.klines[0]

    def desc(self) -> str:
        kl = self.order_block_kline
        zh = "多" if self.side == "long" else "空"
        return f"{zh} {utils.format_datetime(kl.opening_time)} [{kl.lowest_price} - {kl.highest_price}]"

    def test(self, kline: KLine) -> bool:
        # 订单块范围之前的kline不可以被测试
        if kline.opening_time <= self.klines[-1].opening_time:
            return False

        if self.side == "long":
            return kline.lowest_price <= self.order_block_kline.highest_price
        return kline.highest_price >= self.order_block_kline.lowest_price


class MergedOrderBlock(BaseOrderBlock):
    order_blocks: list[OrderBlock]
