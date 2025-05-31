from typing import Literal

from pydantic import BaseModel

from bitcoin.entanglement_theory.schema import (
    Direction,
    Fractal,
    FractalType,
    Pen,
)
from bitcoin.trading.schema.base import (
    GenericKline,
    KLine,
    MergedKline,
)


def _parse_fractal(
    k1: GenericKline, k2: GenericKline, k3: GenericKline
) -> Fractal | None:
    # 顶分型
    if (
        k2.highest_price > k1.highest_price
        and k2.highest_price > k3.highest_price
        # and k2.lowest_price > k1.lowest_price
        and k2.lowest_price > k3.lowest_price
    ):
        return Fractal(type=FractalType.top, k1=k1, k2=k2, k3=k3)
    # 底分型
    if (
        k2.lowest_price < k1.lowest_price
        and k2.lowest_price < k3.lowest_price
        # and k2.highest_price < k1.highest_price
        and k2.highest_price < k3.highest_price
    ):
        return Fractal(type=FractalType.bottom, k1=k1, k2=k2, k3=k3)

    return None


def _merge_kline(
    k1: GenericKline, k2: GenericKline, direction: Direction
) -> MergedKline | None:
    assert k2.opening_time > k1.opening_time
    if (
        k1.highest_price >= k2.highest_price
        and k1.lowest_price <= k2.lowest_price
    ):
        # 满足合并条件, k1合并k2
        klines = []
        for k in (k1, k2):
            if isinstance(k, KLine):
                klines.append(k)
            elif isinstance(k, MergedKline):
                assert k.direction == direction
                klines.extend(k.klines)
            else:
                raise TypeError

        return MergedKline(direction=direction, klines=klines)
    return None


class PenParserOptions(BaseModel):
    """笔解析配置"""

    kline_contains_type: Literal["none", "all", "fractal"] = "all"
    """k线包含处理方式
    none: 不处理包含
    all: 处理全部包含
    fractal: 只处理分型的包含
    """

    min_kline_count: int = 5
    """构成笔的最少k"""

    pen_contains_type: Literal["none", "all"] = "none"
    """处理笔包含的方式
    none: 不处理
    all: 处理所有包含的笔
    """


def _create_empty_pen(fractal: Fractal) -> Pen:
    assert fractal.k3
    return Pen(
        start=fractal,
        middle=[fractal.k2, fractal.k3],
        end=Fractal(
            type=fractal.type.mutex(),
            k1=fractal.k2,
            k2=fractal.k3,
            k3=None,
        ),
    )


class _BasePenResolver(object):
    def __init__(self, options: PenParserOptions | None = None):
        self.klines: list[GenericKline] = []
        self.direction: Direction | None = None
        self.pens = []
        self.current_pen: Pen | None = None
        self.options = options

    def update(self, kline: KLine):
        if self.direction is None and len(self.klines) <= 2:
            # 1. k线没有超过3根(无法分辨分形)
            # 2. 没有包含方向(无法包含, 分形的出现, 一定会有方向)
            self._append_kline(kline)
            return
        self._update(kline)

    def _append_kline(self, kline: GenericKline):
        self.klines.append(kline)
        if len(self.klines) <= 1:
            return
        prev = self.klines[-1]
        if (
            kline.highest_price >= prev.highest_price
            and kline.lowest_price <= prev.lowest_price
        ):
            # 两种情况, 都不修改原来的方向
            # 1. 有方向, 但是两根k的高点和低点相等
            # 2. 初始化的情况, 初始化没有方向, 就没有合并
            # 可能出现高点更高, 低点更低
            return
        elif kline.highest_price >= prev.highest_price:
            self.direction = Direction.up
        else:
            assert kline.lowest_price <= prev.lowest_price
            self.direction = Direction.down

    def _update(self, kline: GenericKline):
        raise NotImplementedError

    def _merge_kline(
        self, k1: GenericKline, k2: GenericKline
    ) -> MergedKline | None:
        if self._contain_kline(k1, k2) or self._contain_kline(k2, k1):
            # k1合并k2
            if isinstance(k1, MergedKline):
                assert k1.direction == self.direction
                klines = k1.klines
            else:
                klines = [k1]
            return MergedKline(direction=self.direction, klines=[*klines, k2])

        return None

    @staticmethod
    def _contain_kline(first: GenericKline, second: GenericKline) -> bool:
        return (first.highest_price >= second.highest_price) and (
            first.lowest_price <= second.lowest_price
        )


class _PenResolver(_BasePenResolver):
    def _update(self, kline: GenericKline):
        assert self.direction
        k1, k2 = self.klines[-2:]
        merged_kline = self._merge_kline(k2, kline)
        if merged_kline is not None:
            # 可以合并k线, 就不往后解析了
            return
        try:
            fractal = _parse_fractal(k1, k2, kline)
            if fractal is None:
                # 没有出现分型, 笔还在延续
                return
            if not self.current_pen:
                # 初始化场景
                assert not self.pens
                self.current_pen = _create_empty_pen(fractal)
                return
            current_pen = self.current_pen
            # 理论上不可能出现同向分型, 因为同向的前提是已经出现了反方向分型
            assert fractal.type != current_pen.end.type

            if current_pen.end.type == FractalType.top:
                # 出现了底分型
                if (
                    kline.highest_price
                    >= current_pen.end.fractal_kline.highest_price
                ):
                    # 最新k超过了笔起点
                    pass
            if len(current_pen.middle) < self.options.min_kline_count:
                pass
        finally:
            self._append_kline(kline)


class PenParser(object):
    def __init__(
        self,
        klines: list[GenericKline] | None = None,
        options: PenParserOptions | None = None,
    ):
        self.options = options
        self.klines: list[GenericKline] = []
        self.pens: list[Pen] = []

        # 当前正在延续的笔. 起点是上一个笔的结束分型k
        self.current_pen: Pen | None = None
        self.parse(klines)

    def parse(self, klines: list[KLine]):
        if klines:
            for kline in klines:
                self.update(kline)

    def update(self, kline: GenericKline):
        # 最少需要有两根k
        # if kline.opening_time.strftime("%Y-%m-%d") == "2022-11-28":
        #     print(kline)

        self.klines.append(kline)
        if len(self.klines) < 3:
            return

        k1, k2, k3 = self.klines[-3:]
        direction = self._get_current_direction()
        if not direction:
            return
        merged_kline = _merge_kline(k2, k3, direction)
        if merged_kline:
            # 如果可以合并
            self.klines[-2:] = [merged_kline]
            return

        fractal = _parse_fractal(k1, k2, k3)
        current_pen = self.current_pen

        if not fractal:
            if current_pen:
                current_pen.middle.append(kline)
            # 还在延续
            prev_kline = self.klines[-1]
            if isinstance(prev_kline, MergedKline):
                # 如果上一根合并了, 则解除合并
                self.klines.pop(-1)
                self.klines.extend(prev_kline.klines)
            return

        # 不可合并, 说明分型已经出现
        if current_pen is None:
            # 初始化场景, 第一次出现分型
            assert not self.pens
            self.current_pen = _create_empty_pen(fractal)
            return

        # 如果出现的分型和当前笔的结束分型类型一样, 则可能是当前笔的延长或者无效分型
        if current_pen.end.type == fractal.type:
            if not self._is_continue(current_pen.end, fractal):
                # 不是延长的底分型属于中间k, 不处理
                current_pen.middle.append(kline)
            elif len(current_pen.middle) < self.options.min_kline_count:
                # 延长并且中间k小于5根, 底分型无效
                current_pen.middle.append(kline)
                current_pen.end = fractal
            else:
                # 笔确认
                current_pen.end = fractal
                self.pens.append(current_pen)
                self.current_pen = _create_empty_pen(fractal)
        else:
            # 说明分型和起点的k是一样的
            if not self._is_continue(current_pen.start, fractal):
                # 中间k, 不用处理
                current_pen.middle.append(kline)
            else:
                # 说明当前笔失效, 出现的是上一笔的延续
                if self.pens:
                    prev_pen = self.pens[-1]
                    prev_pen.end = fractal
                    prev_pen.middle.pop(-1)
                    prev_pen.middle.extend(current_pen.middle)
                self.current_pen = _create_empty_pen(fractal)

    def print_pen(self):
        for pen in self.pens:
            print(pen)
        if self.current_pen:
            print(f"{self.current_pen = }")

    def _get_current_direction(
        self, skip_last: bool = True
    ) -> Direction | None:
        if not self.klines:
            return None

        it = reversed(self.klines)
        if skip_last:
            next(it)

        try:
            kline = next(it)
            for prev_kline in it:
                direction = self._get_direction(prev_kline, kline)
                if direction:
                    return direction
                kline = kline
        except StopIteration:
            return None
        return None

    @staticmethod
    def _get_direction(k1: KLine, k2: KLine) -> Direction | None:
        if (
            k2.highest_price > k1.highest_price
            and k2.lowest_price > k1.lowest_price
        ):
            return Direction.up
        if (
            k2.lowest_price < k1.lowest_price
            and k2.highest_price < k1.highest_price
        ):
            return Direction.down
        return None

    @staticmethod
    def _is_continue(f1: Fractal, f2: Fractal):
        # f2是否是f1的延续
        if f1.type != f2.type:
            return False
        if f1.type == FractalType.top:
            return f2.k2.highest_price >= f1.k2.highest_price
        return f2.k2.lowest_price <= f1.k2.lowest_price
