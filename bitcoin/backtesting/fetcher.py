import asyncio
import datetime
from itertools import batched
from typing import cast

import pandas as pd
from ccxt import Exchange
from loguru import logger
from tortoise import Tortoise

from bitcoin.backtesting.models import HistoryKLine
from bitcoin.conf import settings
from bitcoin.trading.schema.base import KLine


async def init_db():
    path = settings.project_path / "db" / "history.db"
    path.parent.mkdir(exist_ok=True)
    logger.info(f"Initializing database at {path!s}")

    await Tortoise.init(
        config={
            "connections": {
                "default": {
                    "engine": "tortoise.backends.sqlite",
                    "credentials": {
                        "file_path": str(path),
                    },
                }
            },
            "apps": {
                "models": {
                    "models": ["bitcoin.backtesting.models"],
                    "default_connection": "default",
                }
            },
            "debug": True,
            "timezone": settings.zone_info.key,
        }
    )
    logger.info("Generating schema...")
    # generate_schemas 会根据定义的模型创建表 (如果表不存在)
    await Tortoise.generate_schemas()
    logger.info("Database initialized and schema generated.")


class KLine1MFetcher(object):
    def __init__(
        self,
        symbol: str,
        exchange: Exchange | None = None,
        product_type: str = "USDT-FUTURES",
    ):
        self.symbol = symbol
        assert product_type == "USDT-FUTURES"
        self.product_type = product_type
        self.exchange = exchange or settings.create_async_exchange_public(
            "binance"
        )
        self._delta = datetime.timedelta(minutes=1)
        self._offset = datetime.timedelta(minutes=30)

    async def fetch_klines(
        self,
        start: datetime.datetime,
        until: datetime.datetime | None = None,
    ) -> list[KLine]:
        now = datetime.datetime.now(tz=settings.zone_info)
        assert start < now
        if until is None:
            until = now
        assert until > start
        logger.info(f"fetch klines ({start} - {until})")
        current_klines_task = asyncio.create_task(
            self._get_current_1m_klines()
        )
        logger.info("resolving exist klines in db")
        history_klines_in_db = await self._get_klines_from_db(start, until)
        logger.info(f"{len(history_klines_in_db) = }")
        if not history_klines_in_db:
            return await self._fetch_klines_from_api_save_db(start, until)
        result = []
        first_history_kline = history_klines_in_db[0]
        if first_history_kline.opening_time > start:
            left_klines = await self._fetch_klines_from_api_save_db(
                start, first_history_kline.opening_time - self._delta
            )
            assert (
                first_history_kline.opening_time - left_klines[-1].opening_time
            ) == self._delta
            result.extend(left_klines)
        result.extend(history_klines_in_db)

        last_history_kline = history_klines_in_db[-1]
        if (
            last_history_kline.opening_time < until
            and until - last_history_kline.opening_time >= self._delta
        ):
            # until多查一根1分钟k, 因为相同的话没有数据返回

            right_klines = await self._fetch_klines_from_api_save_db(
                last_history_kline.opening_time + self._delta,
                until + self._delta,
                not_closed_kline=(await current_klines_task)[-1].opening_time,
            )
            result.extend(right_klines)
            assert (
                right_klines[0].opening_time - last_history_kline.opening_time
            ) == self._delta
        return result

    async def _fetch_klines_from_api_save_db(
        self,
        start: datetime.datetime,
        until: datetime.datetime,
        not_closed_kline: datetime.datetime | None = None,
    ) -> list[KLine]:
        klines = await self._fetch_klines_from_api(start, until)
        if not_closed_kline:
            logger.warning(f"ignore not closed kline: {not_closed_kline}")

        history_klines = []
        for kline in klines:
            if not_closed_kline and kline.opening_time >= not_closed_kline:
                continue
            data = kline.model_dump()
            data["opening_time"] = kline.opening_time
            history_kline = HistoryKLine(**data, symbol=self.symbol)
            history_klines.append(history_kline)
        if history_klines:
            logger.info(
                f"==== start save {self.symbol} 1m klines in db "
                f"({start} - {until}) ({len(history_klines) = }) ======"
            )

            for chunk in batched(history_klines, 2000):
                await HistoryKLine.bulk_create(chunk)
            logger.info(
                f"==== save klines in db success ({start} - {until}) ===="
            )
        return klines

    async def _fetch_klines_from_api(
        self,
        start: datetime.datetime,
        until: datetime.datetime,
    ):
        logger.warning(
            f"==== download {self.symbol} 1m klines ({start} - {until}) ======"
        )

        klines = [
            KLine.from_ccxt(ohlcv)
            for ohlcv in await self.exchange.fetch_ohlcv(
                symbol=self.symbol,
                timeframe="1m",
                since=int(start.timestamp() * 1000),
                params={
                    "until": int(until.timestamp() * 1000),
                    "paginate": True,
                    "paginationCalls": 20000000,
                },
            )
        ]
        if not klines:
            raise RuntimeError
        prev_kline: KLine | None = None
        for kline in klines:
            if prev_kline is not None:
                assert (
                    kline.opening_time - prev_kline.opening_time == self._delta
                )
            prev_kline = kline
        logger.info(
            f"==== fetch {self.symbol} {len(klines) = } 1m klines "
            f"({start} - {until}) ====="
        )
        return klines

    async def _get_current_1m_klines(self) -> list[KLine]:
        return [
            KLine.from_ccxt(ohlcv)
            for ohlcv in await self.exchange.fetch_ohlcv(
                symbol=self.symbol,
                timeframe="1m",
            )
        ]

    async def _get_klines_from_db(
        self,
        start: datetime.datetime,
        until: datetime.datetime,
    ) -> list[HistoryKLine]:
        result = []
        prev_history_kline: HistoryKLine | None = None
        async for history_kline in HistoryKLine.filter(
            symbol=self.symbol,
            opening_time__gte=start,
            opening_time__lte=until,
        ).order_by("opening_time"):
            history_kline = cast(HistoryKLine, history_kline)
            result.append(history_kline)
            if prev_history_kline is not None:
                is_valid = (
                    history_kline.opening_time
                    - prev_history_kline.opening_time
                ) == self._delta
                if not is_valid:
                    raise RuntimeError
            prev_history_kline = history_kline
        return result


async def main():
    await init_db()
    fetcher = KLine1MFetcher(symbol="BTC/USDT:USDT")
    klines = await fetcher.fetch_klines(
        datetime.datetime.strptime(
            "2025-05-07 00:30:00", "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=settings.zone_info),
    )
    logger.info("load df")
    df = pd.DataFrame(
        [{"time": kline.opening_time, "value": kline} for kline in klines],
    )
    logger.info("set index and sort index")
    df.set_index("time", inplace=True)
    df.sort_index(inplace=True)

    logger.info("start search klines")
    start = datetime.datetime.strptime(
        "2025-05-07 02:30:00", "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=settings.zone_info)
    end = datetime.datetime.now(tz=settings.zone_info)
    for kline in df[start:end]["value"]:
        print(kline.opening_time)

    await fetcher.exchange.close()
    await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(main())
