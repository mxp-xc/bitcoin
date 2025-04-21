# -*- coding: utf-8 -*-
import datetime
from enum import StrEnum
from typing import Literal

from ccxt.base.types import PositionSide
from pydantic import AliasGenerator, BaseModel, ConfigDict, Field, PositiveInt
from pydantic.alias_generators import to_camel, to_snake

from bitcoin.trading import utils


class ToCamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=AliasGenerator(
            serialization_alias=to_snake, validation_alias=to_camel
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
    margin_mode: Literal[
        "crossed", "isolated"
    ]  # 保证金模式: crossed(全仓), isolated(逐仓)
    margin_size: float  # 保证金
    leverage: PositiveInt  # 杠杆倍数
    pos_mode: Literal["one_way_mode", "hedge_mode"]  # 持仓模式(单向, 双向)
    open_price_avg: float  # 平均开仓价
    mark_price: float  # 标记价格
    unrealized_pl: float = Field(
        ..., validation_alias="unrealizedPL"
    )  # 未实现盈亏
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
        k = (
            sign
            * (1 - (self.mark_price / self.open_price_avg))
            * self.leverage
            * 100
        )

        return (
            f"Position("
            f"币种={self.symbol}({self.leverage}x), "
            f"持仓方向={self.hold_side}, "
            f"保证金={round(self.margin_size, 4)}, "
            f"未实现盈亏={self.unrealized_pl}, "
            f"回报率={round(k, 2)}%"
            f")"
        )

    __str__ = as_zh_str

    __repr__ = as_zh_str


class KLine(BaseModel):
    opening_time: datetime.datetime  # 开盘时间
    opening_price: float  # 开盘价格
    closing_price: float  # 收盘价(当前K线未结束的及为最新价)
    highest_price: float
    lowest_price: float
    volume: float  # 成交量

    model_config = ConfigDict(
        json_encoders={datetime.datetime: utils.format_datetime}
    )

    @classmethod
    def from_ccxt(cls, kline: list):
        return cls(
            opening_time=datetime.datetime.fromtimestamp(
                int(int(kline[0]) / 1000)
            ),
            opening_price=kline[1],
            highest_price=kline[2],
            lowest_price=kline[3],
            closing_price=kline[4],
            volume=kline[5],
        )

    @property
    def center_price(self):
        return (self.lowest_price + self.highest_price) / 2

    @property
    def delta_price(self):
        return self.highest_price - self.lowest_price

    @property
    def entity_highest_price(self):
        return max(self.opening_price, self.closing_price)

    @property
    def entity_lowest_price(self):
        return min(self.opening_price, self.closing_price)

    @property
    def side(self) -> PositionSide:
        if self.opening_price >= self.closing_price:
            return "long"
        return "short"

    def get_undulate(self, side: PositionSide | None = None):
        """获取k线方向的振幅"""
        side = side or self.side
        if side == "long":
            return utils.get_undulate(self.lowest_price, self.highest_price)
        return utils.get_undulate(self.highest_price, self.lowest_price)

    def get_undulate_percent(self, side: Position | None = None):
        return self.get_undulate(side) * 100

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


class Direction(StrEnum):
    """方向. 向上或者向下"""

    up = "up"
    down = "down"


class MergedKline(BaseModel):
    direction: Direction  # 合并的方向
    klines: list[KLine]

    @property
    def volume(self) -> float:
        return sum(kline.volume for kline in self.klines)

    @property
    def opening_time(self) -> datetime.datetime:
        return self.klines[0].opening_time

    @property
    def highest_price_kline(self):
        if self.direction == Direction.up:
            return max(self.klines, key=lambda x: x.highest_price)
        return min(self.klines, key=lambda x: x.highest_price)

    @property
    def highest_price(self) -> float:
        return self.highest_price_kline.highest_price

    @property
    def lowest_price_kline(self):
        if self.direction == Direction.up:
            return max(self.klines, key=lambda x: x.lowest_price)
        return min(self.klines, key=lambda x: x.lowest_price)

    @property
    def lowest_price(self) -> float:
        return self.lowest_price_kline.lowest_price


type GenericKline = MergedKline | KLine


class BaseOrderBlock(BaseModel):
    side: PositionSide


class OrderBlock(BaseOrderBlock):
    klines: list[KLine]
    first_test_kline: KLine | None = None

    model_config = ConfigDict(
        json_encoders={datetime.datetime: utils.format_datetime}
    )

    def __str__(self):
        return f"OrderBlock(side={self.side}, kline={self.order_block_kline})"

    __repr__ = __str__

    def get_fvg_percent(self) -> list[float]:
        """获取订单块的fvg"""
        klines = self.klines
        assert len(self.klines) >= 3
        result = []
        start, end = 0, len(klines)
        while start <= end - 3:
            k1, k3 = klines[start], klines[start + 2]
            if self.side == "long":
                fvg = k3.lowest_price - k1.highest_price
            else:
                fvg = k1.lowest_price - k3.highest_price
            result.append((fvg / k1.highest_price) * 100)
            start += 1
        return result

    @property
    def identity(self) -> str:
        return utils.format_datetime(self.start_datetime)

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
