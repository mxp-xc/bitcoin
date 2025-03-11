# -*- coding: utf-8 -*-
import asyncio
import datetime
import uuid
from typing import Literal, Annotated, TYPE_CHECKING

from ccxt.base.types import Position, OrderSide, Num
from ccxt.pro import Exchange
from loguru import logger
from pydantic import BaseModel, Field, ConfigDict

from conf import settings
from trading.helper import OrderBlockParser
from trading.schema.base import OrderBlock, KLine

if TYPE_CHECKING:
    # for dev
    from ccxt.pro.bitget import bitget as Exchange  # noqa


def _client_oid_default_factory() -> str:
    uid = str(uuid.uuid4()).replace("-", "")
    return f"ob-test-{uid}"


class OrderInfo(BaseModel):
    size: str
    side: Literal["buy", "sell"]
    trade_side: Annotated[Literal["open"], Field(serialization_alias="tradeSide")]
    preset_stop_surplus_price: Annotated[str | int, Field(serialization_alias="presetStopSurplusPrice")]
    preset_stop_loss_price: Annotated[str | int, Field(serialization_alias="presetStopLossPrice")]
    price: str
    force: Literal["post_only", "gtc", "fok", "ioc"] | None = None
    order_type: Annotated[Literal["limit", "market"], Field(serialization_alias="orderType")]
    client_oid: Annotated[
        str | None,
        Field(serialization_alias="clientOid", default_factory=_client_oid_default_factory)
    ]
    reduce_only: Annotated[Literal["YES", "NO"] | None, Field(None, serialization_alias="reduceOnly")]


class PlaceOrderContext(BaseModel):
    order_block_parser: OrderBlockParser
    symbol: str
    product_type: str

    model_config = ConfigDict(
        arbitrary_types_allowed=True
    )


class Runner(object):
    def __init__(
        self,
        symbol: str,
        product_type: str,
        exchange: Exchange,
        coin_size: float = 0.001,
        timeframe: str = "30m",
    ):
        self.timeframe = timeframe
        self._coin_size = coin_size
        self._symbol = symbol
        self._product_type = product_type
        self.exchange = exchange

    async def run(self):
        prepare_task = asyncio.create_task(self.exchange.watch_ohlcv(self._symbol, self.timeframe))
        logger.info(f"获取合约账号信息({self._product_type})")
        balances = await self.exchange.fetch_balance({
            "type": "swap",
            "productType": self._product_type
        })
        if self._product_type.startswith("S"):
            balance = balances["SUSDT"]
        else:
            balance = balances["USDT"]
        margin_coin = balances["info"][0]["marginCoin"]
        logger.info(
            f"当前账号可用({margin_coin}): {balance['free']}, "
            f"已用: {balance['used']}, "
            f"总计: {balance['total']}, "
        )

        last_run_datetime = datetime.datetime.now()
        await self.run_once()

        await prepare_task
        while True:
            ohlcv = await self.exchange.watch_ohlcv(self._symbol, self.timeframe)
            kline = KLine.from_ccxt(ohlcv[-1])
            if kline.opening_time > last_run_datetime:
                await self.run_once()
                last_run_datetime = kline.opening_time
            else:
                print(f"\rwait: {kline}", end="")

    async def _get_klines(self) -> list[KLine]:
        ohlcv = await self.exchange.fetch_ohlcv(
            symbol=self._symbol,
            timeframe=self.timeframe,
            since=int((datetime.datetime.now() - datetime.timedelta(days=60)).timestamp() * 1000),
            params={
                "until": int(datetime.datetime.now().timestamp() * 1000)
            },
            limit=1000
        )
        return [KLine.from_ccxt(d) for d in ohlcv]

    async def _init_ob_parser(self):
        ob_parser = OrderBlockParser(timeframe=self.timeframe)
        logger.info(f"读取`{self._symbol}`k线分析订单块中, 时间级别为: {self.timeframe}")
        klines = await self._get_klines()
        # dt = datetime.datetime.now().replace(hour=2, minute=7)
        # klines = list(filter(lambda k: k.opening_time <= dt, klines))
        for kline in klines[:-1]:
            ob_parser.fetch(kline)
        return ob_parser, klines

    async def _get_position_map(self) -> dict[str, Position] | None:
        """返回多空仓, 左边是多, 右边是空, 不存在则返回None"""
        positions = await self.exchange.fetch_positions([self._symbol])
        if not positions:
            return {}
        return {
            position['side']: position
            for position in positions
        }

    async def _cancel_all_orders(self):
        """撤销所有委托"""
        logger.info("准备撤销所有非持仓委托")
        orders = await self.exchange.fetch_open_orders(self._symbol)
        if orders:
            await self.exchange.cancel_all_orders(self._symbol)
        logger.info("撤销完成")

    async def _create_order(
        self,
        side: OrderSide,
        price: Num,
        amount: float | None = None,
        preset_stop_surplus_price: Num = None,
        preset_stop_loss_price: Num = None,
    ):
        """下单"""
        params = {"tradeSide": "open"}
        if preset_stop_surplus_price:
            params["takeProfit"] = {"stopPrice": preset_stop_surplus_price}
        if preset_stop_loss_price:
            params["stopLoss"] = {"stopPrice": preset_stop_loss_price}
        await self.exchange.create_limit_order(
            self._symbol,
            side,
            amount or self._coin_size,
            price=price,
            params=params
        )

    async def run_once(self):
        # 当前持仓数据
        logger.info("run once")
        await self._cancel_all_orders()
        position_map = await self._get_position_map()
        if 'short' in position_map and 'long' in position_map:
            logger.warning("当前有多空持仓, 不执行策略")
            return

        ob_parser, klines = await self._init_ob_parser()
        order_blocks: dict[str, OrderBlock] = dict(ob_parser.order_blocks)
        if not order_blocks:
            logger.info("未发现订单块")
            return

        last_kline = klines[-1]
        # logger.info(f"订单块分析完成, 当前最新价格: {last_kline.closing_price}")
        # for ob in order_blocks.values():
        #     assert ob.side
        #     logger.info(ob.desc())
        long_order_blocks = sorted(
            (ob for ob in ob_parser.order_blocks.values() if ob.side == "long"),
            key=lambda o: o.order_block_kline.highest_price,
            reverse=True
        )
        short_order_blocks = sorted(
            (ob for ob in ob_parser.order_blocks.values() if ob.side == "short"),
            key=lambda o: o.order_block_kline.lowest_price,
        )
        if 'long' not in position_map and long_order_blocks:
            # 存在多单订单块并且没有多单持仓, 才下订单
            last_order_block = long_order_blocks[0]
            kline = last_order_block.order_block_kline
            await self._create_order(
                'buy',
                kline.highest_price,
                preset_stop_surplus_price=kline.highest_price + kline.delta_price,
                preset_stop_loss_price=kline.lowest_price
            )

        if 'short' not in position_map and short_order_blocks:
            # 存在空单订单块并且没有多单持仓, 才下订单
            last_order_block = short_order_blocks[0]
            kline = last_order_block.order_block_kline
            await self._create_order(
                'sell',
                kline.lowest_price,
                preset_stop_surplus_price=kline.lowest_price - kline.delta_price,
                preset_stop_loss_price=kline.highest_price
            )


async def main():
    async with settings.create_async_exchange() as exchange:
        exchange.set_sandbox_mode(True)
        runner = Runner(
            symbol="SBTCSUSDT",
            product_type="SUSDT-FUTURES",
            timeframe="1m",
            exchange=exchange
        )
        await runner.run()


if __name__ == '__main__':
    asyncio.run(main())
