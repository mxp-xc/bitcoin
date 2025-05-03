from enum import StrEnum

from pydantic import BaseModel

from bitcoin.trading.schema.base import Direction, GenericKline, KLine


class FractalType(StrEnum):
    """分型"""

    top = "top"
    bottom = "bottom"

    def mutex(self) -> "FractalType":
        if self == FractalType.top:
            return FractalType.bottom
        return FractalType.top


class Fractal(BaseModel):
    """分型"""

    type: FractalType
    k1: GenericKline | None
    k2: GenericKline
    k3: GenericKline | None

    @property
    def price(self) -> float:
        if self.type == FractalType.top:
            return self.k2.highest_price
        return self.k2.lowest_price

    @property
    def fractal_kline(self) -> GenericKline:
        return self.k2

    @property
    def unwrap_fractal_kline(self) -> KLine:
        fractal_kline = self.fractal_kline
        if isinstance(fractal_kline, KLine):
            return fractal_kline
        if self.type == FractalType.top:
            return fractal_kline.highest_price_kline
        return fractal_kline.lowest_price_kline

    @property
    def is_unfinished(self):
        """是否是未完成分型"""
        return self.k3 is None

    def __str__(self):
        return (
            f"Fractal({self.type}, {self.unwrap_fractal_kline.opening_time})"
        )

    __repr__ = __str__


class Pen(BaseModel):
    """一笔"""

    start: Fractal  # 开始分型
    middle: list[GenericKline]  # 分型的中间k线
    end: Fractal  # 结束分型, 没有则表示还在走

    @property
    def is_unfinished(self):
        """是否是未完成笔"""
        return self.end.is_unfinished

    @property
    def direction(self) -> Direction:
        """笔的方向"""
        if self.start.type == FractalType.top:
            return Direction.down
        return Direction.up

    def __str__(self):
        start = self.start.unwrap_fractal_kline.opening_time
        end = self.end.unwrap_fractal_kline.opening_time
        return f"Pen({start} - {end})"

    __repr__ = __str__
