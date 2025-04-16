import asyncio
import datetime
import os
from collections import defaultdict

import aiohttp
from ccxt.async_support import Exchange
from loguru import logger
from pydantic import BaseModel

from bitcoin.conf import settings
from bitcoin.trading.helper import KLineWatcher
from bitcoin.trading.schema.base import KLine
from bitcoin.trading.utils import send_wx_message_or_log, format_datetime


class HeatMap(BaseModel):
    time: datetime.datetime
    long: tuple[tuple[int, float], ...]
    short: tuple[tuple[int, float], ...]


def get_side_desc(side: str):
    if side == "long":
        return '<font color="info">多单</font>'
    assert side == "short"
    return '<font color="warning">空单</font>'


class HeatmapWatcher(object):
    def __init__(self, threshold: int, wx_key: str | None = None):
        self.session: aiohttp.ClientSession | None = None
        self.exchange: Exchange | None = None
        self.threshold = threshold
        self._wx_key = wx_key
        self._heatmaps: list[HeatMap] | None = None
        self._heatmap_lock = asyncio.Lock()
        self._stopping = False

    async def _run(self):
        await send_wx_message_or_log(f"start watch heatmap. threshold: {self.threshold}", key=self._wx_key)
        kline_watcher = KLineWatcher(self.exchange)
        self._heatmaps = await self._get_heatmaps_with_lock()
        asyncio.create_task(self._refresh_heatmap_forever(60))
        time_seen: dict[datetime.datetime, set[str]] = defaultdict(set)
        heatmap_refresh_retry = 0
        async for wrapper in kline_watcher.async_iter(
            symbol="BTC/USDT:USDT",
            timeframe="1m"
        ):
            if self._stopping:
                break
            if wrapper.initialize:
                continue
            kline = wrapper.kline
            # 当前1分钟k的波动范围
            heatmaps = self._heatmaps
            it = reversed(heatmaps)
            for heatmap in it:
                if heatmap.time == kline.opening_time:
                    break
            else:
                # 可能刚收线, 重试三次
                logger.info(
                    f"refresh heatmap. "
                    f"last heatmap time: {heatmaps[-1].time}. "
                    f"current kline open time: {kline.opening_time}. "
                    f"heatmap_refresh_retry: {heatmap_refresh_retry}"
                )
                if heatmap_refresh_retry > 3:
                    raise RuntimeError
                self._heatmaps = await self._get_heatmaps_with_lock()
                heatmap_refresh_retry += 1
                continue
            heatmap_refresh_retry = 0
            try:
                prev_heatmap = next(it)
            except StopIteration:
                raise RuntimeError
            seen = time_seen[kline.opening_time]
            for side, price_size in (
                ("long", heatmap.long),
                ("short", heatmap.short)
            ):
                for index, (price, size) in enumerate(price_size):
                    if (
                        size < self.threshold
                        or price < kline.lowest_price or price > kline.highest_price
                    ):
                        continue
                    key = f"{side}-{price}"
                    if key in seen:
                        # 已经告警过了
                        continue
                    await send_wx_message_or_log(
                        f"{format_datetime(kline.opening_time)}"
                        f"在{price}出现穿透大额{get_side_desc(side)}: {size}.\n"
                        f"上一根k的挂单: {prev_heatmap.long[index][1]},\n"
                        f"交易商成交量: {kline.volume}",
                        key=self._wx_key
                    )
                    seen.add(key)

            if wrapper.closed:
                # 收线后, 清除缓存, 并且记录这跟k的成交量以便参考
                has_data = False
                for value in time_seen.values():
                    if value:  # 存在数据
                        has_data = True
                if has_data:
                    await self.alert_close_kline_detail(kline)
                time_seen.clear()

    async def alert_close_kline_detail(self, kline: KLine):
        # 获取最新的heatmaps
        logger.info(f"alert_close_kline_detail for {kline}")
        heatmaps = self._heatmaps = await self._get_heatmaps_with_lock()
        kline_time = format_datetime(kline.opening_time)
        for heatmap in heatmaps:
            if heatmap.time == kline.opening_time:
                break
        else:
            logger.warning(f"没有找到收线对应的heatmap: {kline}")
            await send_wx_message_or_log(f"{kline_time}收线, 成交量: {kline.volume}", key=self._wx_key)
            return

        seen: dict[str, float] = {}
        for side, price_size in (
            ("long", heatmap.long),
            ("short", heatmap.short)
        ):
            for index, (price, size) in enumerate(price_size):
                if (
                    size < self.threshold
                    or price < kline.lowest_price or price > kline.highest_price
                ):
                    continue
                key = f"{side}-{price}"
                seen[key] = size

        messages = []
        total_size = 0
        for key, size in seen.items():
            side, price = key.split("-", 1)
            total_size += size
            messages.append(f"{price}{get_side_desc(side)} {size}")
        if not messages:
            await send_wx_message_or_log(
                f"{kline_time}收线重新检测, 不存在穿透的大额挂单",
                key=self._wx_key
            )
            return
        await send_wx_message_or_log(
            f"{kline_time}收线重新检测. 存在穿透的大额挂单:\n"
            f"{'\n'.join(messages)}\n"
            f"总挂单额: {total_size}\n"
            f"交易商成交量: {kline.volume}",
            key=self._wx_key
        )

    async def _refresh_heatmap_forever(self, delay: float = 60):
        if self._heatmaps:
            await asyncio.sleep(delay)
        while not self._stopping:
            self._heatmaps = await self._get_heatmaps_with_lock()
            await asyncio.sleep(delay)

    async def run(self):
        async with (
            aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(verify_ssl=False)
            ) as self.session,
            settings.create_async_exchange_public('binance') as self.exchange
        ):
            try:
                await self._run()
            except Exception as exc:
                await send_wx_message_or_log(
                    f"Failed to run heatmap: {exc!s}",
                    key=self._wx_key,
                    level='exception'
                )
            finally:
                await send_wx_message_or_log("stop heatmap bot", key=self._wx_key)

    async def _get_heatmaps_with_lock(self):
        async with self._heatmap_lock:
            for _ in range(3):
                try:
                    return await self._get_heatmaps()
                except Exception as exc:
                    await send_wx_message_or_log(
                        f"Failed to get heatmap: {exc!s}. wait 1min",
                        level='exception',
                        key=self._wx_key
                    )
                    await asyncio.sleep(60)

    async def _get_heatmaps(self) -> list[HeatMap]:
        now = datetime.datetime.now()
        start_datetime = now - datetime.timedelta(hours=2)
        result = []
        async with self.session.get(
            url="https://capi.coinglass.com/liquidity-heatmap/api/liquidity/v4/heatmap",
            params={
                'symbol': 'Binance_BTCUSDT#heatmap',
                'interval': 'm1',
                'startTime': int(start_datetime.timestamp()),
                'minLimit': 'false',
                'endTime': int(now.timestamp()),
            }
        ) as response:
            data = await response.json()
            heatmap_data = data["data"]["data"]
            # 获取间隔
            counter = defaultdict(int)
            it = iter(heatmap_data)
            prev_ts = next(it)[0]
            for ts, *_ in it:
                delta = int(ts - prev_ts)
                counter[delta] += 1
                prev_ts = ts
            interval = max(counter.items(), key=lambda item: item[1])[0]

            for index, (ts, long, short) in enumerate(heatmap_data):
                dt = datetime.datetime.fromtimestamp(ts)
                # 可能会出现错误数据
                if dt.year < 2000:
                    prev = heatmap_data[index - 1]
                    ts = prev[0] + interval
                    dt = datetime.datetime.fromtimestamp(ts)
                    assert ts > 2000
                result.append(HeatMap(
                    time=dt,
                    long=tuple((int(item[0]), item[1]) for item in long),
                    short=tuple((int(item[0]), item[1]) for item in short)
                ))

        return result


def main():
    wx_key = os.getenv("HEATMAP_WX_KEY")
    if not wx_key:
        logger.info("not config wx key")
    watcher = HeatmapWatcher(threshold=40, wx_key=wx_key)
    asyncio.run(watcher.run())


if __name__ == '__main__':
    main()
