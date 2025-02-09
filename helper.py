import datetime
from collections import OrderedDict

from pydantic import BaseModel

import utils
from api import bitget_api
from schema.base import KLine, OrderBlock


class OrderBlockResult(BaseModel):
    order_blocks: list[OrderBlock]
    tested_order_blocks: list[OrderBlock]


def get_klines(
        symbol: str,
        granularity: str,
        day: int,
        product_type: str = "USDT-FUTURES",
        limit: int = 1000
) -> list[KLine]:
    response = bitget_api.api.get(
        "/api/v2/mix/market/candles",
        params={
            "symbol": symbol,
            "productType": product_type,
            "granularity": granularity,
            "startTime": int((datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=day)).timestamp() * 1000),
            "limit": limit
        }
    )
    return [
        KLine(
            opening_time=datetime.datetime.fromtimestamp(int(int(kline[0]) / 1000)),
            opening_price=kline[1],
            highest_price=kline[2],
            lowest_price=kline[3],
            closing_price=kline[4],
            granularity=granularity
        )
        for kline in response["data"]
    ]


class OrderBlockParser(object):
    def __init__(self, merge: bool = False):
        self.order_blocks: OrderedDict[str, OrderBlock] = OrderedDict()
        self.tested_order_blocks: list[OrderBlock] = []
        self._buffer: list[KLine] = []
        self._merge = merge
        self._current_order_block: OrderBlock | None = None

    def fetch(self, kline: KLine):
        if self._buffer:
            # debug check
            last_kline = self._buffer[-1]
            assert last_kline.granularity == kline.granularity
            assert last_kline.opening_time < kline.opening_time
            if self._current_order_block is not None and kline.granularity == "30m":
                next_time = self._current_order_block.klines[-1].opening_time + datetime.timedelta(minutes=30)
                assert next_time == kline.opening_time

        self._trigger_test(kline)

        self._buffer.append(kline)
        if len(self._buffer) < 3:
            return

        direction = self._parse(*self._buffer[-3:])
        if direction is None:
            # 新出现的kline和之前没有继续形成订单块, 只保留最后的两个, 继续计算
            self._current_order_block = None
            if len(self._buffer) > 2:
                self._buffer = [*self._buffer[-2:]]
            return

        # 当超过3个时, 说明已经出现了ob
        if self._current_order_block:
            self._current_order_block.klines.append(kline)
        else:
            order_block = self._current_order_block = OrderBlock(
                granularity=kline.granularity,
                klines=list(self._buffer),
                direction=direction
            )

            self.order_blocks[utils.format_datetime(order_block.start_datetime)] = order_block
        self._merge_order_block()

    def _trigger_test(self, kline: KLine):
        keys = []
        for key, ob in self.order_blocks.items():
            if self._test_order_block(ob, kline):
                ob.first_test_kline = kline
                keys.append(key)
        for key in keys:
            tested_order_block = self.order_blocks.pop(key)
            self.tested_order_blocks.append(tested_order_block)

    def _test_order_block(self, order_block: OrderBlock, kline: KLine) -> bool:  # noqa
        return order_block.test(kline)

    def _parse(self, k1: KLine, k2: KLine, k3: KLine):  # noqa
        if (
                k2.entity_highest_price >=
                k1.lowest_price >
                k3.highest_price >=
                k2.entity_lowest_price
        ):
            return "down"

        if (
                k2.entity_lowest_price <=
                k1.highest_price <
                k3.lowest_price <=
                k2.entity_highest_price
        ):
            return "up"
        return None

    def _merge_order_block(self):
        if not self._merge:
            return


def get_order_block(symbol: str, granularity: str, day: int) -> OrderBlockResult:
    klines = get_klines(symbol, granularity, day)
    order_block_map: dict[datetime.datetime, OrderBlock] = OrderedDict()
    tested_order_blocks: list[OrderBlock] = []

    ob_kline = []
    direction = None
    new_order_block = None
    for i in range(len(klines) - 2):
        k1, k2, k3 = klines[i: i + 3]

        if (
                int(k2.entity_highest_price) >=
                int(k1.lowest_price) >
                int(k3.highest_price) >=
                int(k2.entity_lowest_price)
        ):
            ob_kline.append(k1)
            direction = "down"
        elif (
                int(k2.entity_lowest_price) <=
                int(k1.highest_price) <
                k3.lowest_price <=
                int(k2.entity_highest_price)
        ):
            ob_kline.append(k1)
            direction = "up"
        elif ob_kline:
            new_order_block = OrderBlock(
                granularity=granularity,
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
            granularity=granularity,
            klines=[*ob_kline, k1, k2],  # noqa
            direction=direction
        )
        order_block_map[new_order_block.start_datetime] = new_order_block

    return OrderBlockResult(
        order_blocks=sorted(order_block_map.values(), key=lambda ob_: ob_.start_datetime),
        tested_order_blocks=sorted(tested_order_blocks, key=lambda ob_: ob_.start_datetime)
    )


if __name__ == '__main__':
    ...
    # index = 0
    # while True:
    #     index += 1
    #     print(f'{index}, {get_klines("BTCUSDT", granularity="30m", day=1, limit=1)}')

    # r = get_order_block("BTCUSDT", day=1, granularity="30m")
    # for od in r.tested_order_blocks:
    #     print(f"订单块 {od.start_datetime} 被测试在k线 {od.first_test_kline.opening_time}")
    #
    # print("=" * 30 + " 剩余未测试订单块 " + "=" * 30)
    # print("\n".join(v.desc() for v in r.order_blocks))

    # obp = OrderBlockParser()
    # for kl in get_klines("BTCUSDT", day=1, granularity="1m"):
    #     obp.fetch(kl)
    # for ob in obp.order_blocks.values():
    #     print(ob.desc())
