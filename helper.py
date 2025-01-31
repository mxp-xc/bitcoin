import datetime
from collections import OrderedDict

import pydantic
from pydantic import BaseModel, ConfigDict

import utils
from api import bitget_api


class KLine(pydantic.BaseModel):
    opening_time: datetime.datetime  # 开盘时间
    opening_price: float  # 开盘价格
    highest_price: float  # 最高价
    lowest_price: float  # 最低价
    closing_price: float  # 收盘价(当前K线未结束的及为最新价)
    model_config = ConfigDict(
        json_encoders={
            datetime.datetime: utils.format_datetime
        }
    )

    @property
    def ehp(self):
        return max(self.opening_price, self.closing_price)

    @property
    def elp(self):
        return min(self.opening_price, self.closing_price)

    @classmethod
    def from_kline_list(cls, kline: list):
        return cls(
            opening_time=datetime.datetime.fromtimestamp(int(int(kline[0]) / 1000)),
            opening_price=kline[1],
            highest_price=kline[2],
            lowest_price=kline[3],
            closing_price=kline[4],
        )

    def as_str_zh(self):
        return (
            f"KLine("
            f"开盘时间={self.opening_time.strftime('%Y-%m-%d %H:%M:%S')}, "
            f"开盘价格={self.opening_price}, "
            f"最高价格={self.highest_price}, "
            f"最低价格={self.lowest_price}, "
            f"收盘价格={self.closing_price}, "
            f"收盘价格={self.closing_price}, "
            f")"
        )

    __str__ = as_str_zh


class OrderBlock(pydantic.BaseModel):
    time_unit: str
    klines: list[KLine]
    direction: str
    first_test_kline: KLine | None = None

    model_config = ConfigDict(
        json_encoders={
            datetime.datetime: utils.format_datetime
        }
    )

    @property
    def start_datetime(self):
        return self.order_block_kline.opening_time

    @property
    def order_block_kline(self) -> KLine:
        return self.klines[0]

    def desc(self) -> str:
        kl = self.order_block_kline
        zh = "多" if self.direction == "up" else "空"
        return f"做{zh} {utils.format_datetime(kl.opening_time)} [{kl.lowest_price} - {kl.highest_price}]"

    def test(self, kline: KLine) -> bool:
        # 订单块范围之前的kline不可以被测试
        if kline.opening_time <= self.klines[-1].opening_time:
            return False

        if self.direction == "up":
            return kline.lowest_price <= self.order_block_kline.highest_price
        return kline.highest_price >= self.order_block_kline.lowest_price


class OrderBlockResult(BaseModel):
    order_blocks: list[OrderBlock]
    tested_order_blocks: list[OrderBlock]


def get_order_block(symbol: str, granularity: str, day: int) -> OrderBlockResult:
    response = bitget_api.get(
        "/api/v2/mix/market/candles",
        params={
            "symbol": symbol,
            "productType": "USDT-FUTURES",
            "granularity": granularity,
            "startTime": int((datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=day)).timestamp() * 1000),
            "limit": 1000
        }
    )
    klines = [
        KLine.from_kline_list(k)
        for k in response["data"]
    ]
    order_block_map: dict[datetime.datetime, OrderBlock] = OrderedDict()
    tested_order_blocks: list[OrderBlock] = []

    ob_kline = []
    direction = None
    new_order_block = None
    for i in range(len(klines) - 2):
        k1, k2, k3 = klines[i: i + 3]

        if (
                int(k2.ehp) >= int(k1.lowest_price) > int(k3.highest_price) >= int(k2.elp)
        ):
            ob_kline.append(k1)
            direction = "down"
        elif (
                int(k2.elp) <= int(k1.highest_price) < k3.lowest_price <= int(k2.ehp)
        ):
            ob_kline.append(k1)
            direction = "up"
        elif ob_kline:
            new_order_block = OrderBlock(
                time_unit=granularity,
                klines=[*ob_kline, k1, k2],
                direction=direction
            )

        test_order_block_keys = []
        for key, ob in order_block_map.items():
            for kl in (k1, k2, k3):
                if ob.test(kl):
                    ob.first_test_kline = kl
                    test_order_block_keys.append(key)
                    tested_order_blocks.append(ob)
                    break

        for key in test_order_block_keys:
            del order_block_map[key]

        if new_order_block:
            order_block_map[new_order_block.start_datetime] = new_order_block
            ob_kline.clear()
            direction = None
            new_order_block = None

    if ob_kline:
        new_order_block = OrderBlock(
            time_unit=granularity,
            klines=[*ob_kline, k1, k2],  # noqa
            direction=direction
        )
        order_block_map[new_order_block.start_datetime] = new_order_block

    return OrderBlockResult(
        order_blocks=sorted(order_block_map.values(), key=lambda ob_: ob_.start_datetime),
        tested_order_blocks=sorted(tested_order_blocks, key=lambda ob_: ob_.start_datetime)
    )


if __name__ == '__main__':
    r = get_order_block("BTCUSDT", day=3, granularity="30m")
    for od in r.tested_order_blocks:
        print(f"订单块 {od.start_datetime} 被测试在k线 {od.first_test_kline.opening_time}")

    print("=" * 30 + " 剩余未测试订单块 " + "=" * 30)
    print("\n".join(v.desc() for v in r.order_blocks))
