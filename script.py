# -*- coding: utf-8 -*-
import asyncio
import datetime
import uuid
from typing import TYPE_CHECKING, Type

from ccxt.base.errors import ExchangeError
from ccxt.base.types import Position, OrderSide, Num
from ccxt.pro import Exchange
from loguru import logger
from pydantic import BaseModel, ConfigDict

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
    side: OrderSide
    price: Num
    amount: float | None = None
    preset_stop_surplus_price: Num = None
    preset_stop_loss_price: Num = None
    # client_oid: Annotated[
    #     str | None,
    #     Field(serialization_alias="clientOid", default_factory=_client_oid_default_factory)
    # ]


class PlaceOrderContext(BaseModel):
    order_blocks: list[OrderBlock]
    mutex_order_blocs: list[OrderBlock]

    model_config = ConfigDict(
        arbitrary_types_allowed=True
    )


class RunnerOption(BaseModel):
    symbol: str
    timeframe: str
    coin_size: float
    min_fvg: int = 0


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
        self.coin_size = coin_size
        self.symbol = symbol
        self.product_type = product_type
        self.exchange = exchange

    async def run(self):
        while True:
            try:
                await self._run()
            except Exception as exc:  # noqa: ignored
                logger.exception(f"Failed to run for: {self.symbol}, {self.timeframe}")
                await asyncio.sleep(3)

    async def _run(self):
        prepare_task = asyncio.create_task(self.exchange.watch_ohlcv(self.symbol, self.timeframe))

        last_run_datetime = datetime.datetime.now()
        await self._run_once()

        await prepare_task
        while True:
            ohlcv = await self.exchange.watch_ohlcv(self.symbol, self.timeframe)
            kline = KLine.from_ccxt(ohlcv[-1])
            if kline.opening_time > last_run_datetime:
                await self._run_once()
                last_run_datetime = kline.opening_time

    async def _get_klines(self) -> list[KLine]:
        ohlcv = await self.exchange.fetch_ohlcv(
            symbol=self.symbol,
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
        logger.info(f"读取`{self.symbol}`k线分析订单块中, 时间级别为: {self.timeframe}")
        klines = await self._get_klines()
        # dt = datetime.datetime.now().replace(hour=2, minute=7)
        # klines = list(filter(lambda k: k.opening_time <= dt, klines))
        for kline in klines[:-1]:
            ob_parser.fetch(kline)
        return ob_parser, klines

    async def _get_position_map(self) -> dict[str, Position]:
        positions = await self.exchange.fetch_positions([self.symbol])
        if not positions:
            return {}
        return {
            position['side']: position
            for position in positions
        }

    async def _cancel_all_orders(self):
        """撤销所有委托"""
        orders = await self.exchange.fetch_open_orders(self.symbol)
        if not orders:
            return
        logger.info("准备撤销所有非持仓委托")

        try:
            await self.exchange.cancel_all_orders(self.symbol)
        except ExchangeError as err:
            if "No order to cancel" in str(err):
                return
            raise
        logger.info("撤销完成")

    async def _resolve_order_info(self, order_block: OrderBlock, context: PlaceOrderContext) -> OrderInfo:
        side = order_block.side
        kline = order_block.order_block_kline
        if side == 'long':
            # 做多订单块
            price = kline.highest_price
            preset_stop_surplus_price = kline.highest_price + kline.delta_price
            preset_stop_loss_price = kline.lowest_price
        else:
            # 做空订单块
            price = kline.lowest_price,
            preset_stop_surplus_price = kline.lowest_price - kline.delta_price,
            preset_stop_loss_price = kline.highest_price

        return OrderInfo(
            side="buy" if side == "long" else "sell",
            price=price,
            preset_stop_surplus_price=preset_stop_surplus_price,
            preset_stop_loss_price=preset_stop_loss_price
        )

    async def _create_order0(self, order_info: OrderInfo):
        """下单"""
        params = {"tradeSide": "open"}
        if order_info.preset_stop_surplus_price:
            params["takeProfit"] = {"stopPrice": order_info.preset_stop_surplus_price}
        if order_info.preset_stop_loss_price:
            params["stopLoss"] = {"stopPrice": order_info.preset_stop_loss_price}
        await self.exchange.create_limit_order(
            self.symbol,
            order_info.side,
            order_info.amount or self.coin_size,
            price=order_info.price,
            params=params
        )

    async def _create_order(
        self,
        order_blocks: list[OrderBlock],
        mutex_order_blocs: list[OrderBlock]
    ):
        context = PlaceOrderContext(
            order_blocks=order_blocks,
            mutex_order_blocs=mutex_order_blocs
        )
        order_block = await self._choice_order_block(context)
        if not order_block:
            return

        order_info = await self._resolve_order_info(order_block, context)
        logger.info(f"{order_info = }")
        await self._create_order0(order_info)

    async def _choice_order_block(self, context: PlaceOrderContext) -> OrderBlock | None:  # noqa
        if context.order_blocks:
            return context.order_blocks[0]
        return None

    def _parse_order_block(self, order_block):
        pass

    async def _run_once(self):
        # 当前持仓数据
        logger.info(f"run once for {self.symbol}, {self.timeframe = }")
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

        # last_kline = klines[-1]
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
            await self._create_order(long_order_blocks, short_order_blocks)

        if 'short' not in position_map and short_order_blocks:
            # 存在空单订单块并且没有多单持仓, 才下订单
            await self._create_order(short_order_blocks, long_order_blocks)


class CustomRunnerOptions(RunnerOption):
    runner_class: Type[Runner]


class RunnerManager(object):
    def __init__(
        self,
        options: list[RunnerOption],
        exchange: Exchange,
        product_type: str
    ):
        self.product_type = product_type
        self.exchange = exchange
        self.options = options

    def _build_runner(self, option: RunnerOption) -> Runner:
        if isinstance(option, CustomRunnerOptions):
            runner_class = option.runner_class
        else:
            runner_class = Runner
        return runner_class(
            symbol=option.symbol,
            product_type=self.product_type,
            exchange=self.exchange,
            timeframe=option.timeframe,
            coin_size=option.coin_size
        )

    async def _get_account_info(self):
        logger.info(f"获取合约账号信息({self.product_type})")
        balances = await self.exchange.fetch_balance({
            "type": "swap",
            "productType": self.product_type
        })
        if self.product_type.startswith("S"):
            balance = balances["SUSDT"]
        else:
            balance = balances["USDT"]
        margin_coin = balances["info"][0]["marginCoin"]
        logger.info(
            f"当前账号可用({margin_coin}): {balance['free']}, "
            f"已用: {balance['used']}, "
            f"总计: {balance['total']}, "
        )
        return balance

    async def run(self):
        await self._get_account_info()
        runners = [self._build_runner(option) for option in self.options]
        await asyncio.gather(*(runner.run() for runner in runners))


class BTCRunner(Runner):
    @staticmethod
    def is_workday():
        """是否是工作日"""
        return datetime.datetime.now().isoweekday() <= 5

    async def _choice_order_block(self, context: PlaceOrderContext) -> OrderBlock | None:
        if not context.order_blocks:
            return None
        for order_block in context.order_blocks:
            klines = order_block.klines
            k1 = order_block.order_block_kline
            if order_block.side == 'long':
                # 多单, 除下影线
                undulate = (k1.delta_price / k1.lowest_price) * 100
            else:
                # 多单, 除上影线
                undulate = (k1.delta_price / k1.highest_price) * 100
            if undulate <= 0.2 or undulate >= 1.5:
                logger.info(f"[振幅: pass] {undulate} <= 0.2 or {undulate} >= 1.5. {k1}")
                continue

            k2 = klines[2]
            if order_block.side == 'long':
                fvg = k2.lowest_price - k1.highest_price
            else:
                fvg = k1.lowest_price - k2.highest_price
            percent = (fvg / k1.highest_price) * 100
            # fvg小于0.1的略过
            if percent < 0.1:
                logger.info(f"[fvg: pass] {percent} < 0.1. {k1}")
                continue
            return order_block
        return None

    async def _resolve_order_info(
        self,
        order_block: OrderBlock,
        context: PlaceOrderContext
    ) -> OrderInfo:
        """
        1. 无论工作日或者周末, 0.2%以下的订单块或者fvg < 0.1的不做
        2. 工作日0.4~0.7的, 边缘入场, 0.7~1.5中轨入场. 止损边缘. 止盈为止损的1.5倍.  大于1.5不做
        3. 周末, 0.4~0.7, 0.7~1.5入场规则和工作日一样. 止损多带0.2, 下单金额为止损总仓位5%的金额. 盈亏1:1
        4. 工作日, 当fvg < 0.05%视为不存在fvg
        """
        kline = order_block.order_block_kline
        if order_block.side == 'long':
            # 多单, 除下影线
            undulate = (kline.delta_price / kline.lowest_price) * 100
        else:
            # 空单, 除上影线
            undulate = (kline.delta_price / kline.highest_price) * 100
        # assert 0.4 < undulate < 1.5
        if undulate > 0.7:
            # 中轨
            price = kline.center_price
        elif order_block.side == 'long':
            # 多上影线
            price = kline.highest_price
        else:
            # 空上影线
            price = kline.lowest_price

        if order_block.side == 'long':
            # 做多止损下影线
            preset_stop_loss_price = kline.lowest_price
            if self.is_workday():
                # 工作日盈亏比1:1.5
                preset_stop_surplus_price = price + 1.5 * (price - preset_stop_loss_price)
            else:
                # 周末需要多带0.2的止损
                preset_stop_loss_price -= 0.2 * price
                # 盈亏比1:1
                preset_stop_surplus_price = price + preset_stop_loss_price
        else:
            # 做空止损上影线
            preset_stop_loss_price = kline.highest_price
            if self.is_workday():
                # 非周末盈亏比1:1.5
                preset_stop_surplus_price = price - 1.5 * (preset_stop_loss_price - price)
            else:
                # 周末需要多带0.2的止损
                preset_stop_loss_price += 0.002 * price
                # 盈亏比1:1
                preset_stop_surplus_price = price - preset_stop_loss_price

        return OrderInfo(
            side='buy' if order_block.side == 'long' else 'sell',
            price=price,
            preset_stop_surplus_price=preset_stop_surplus_price,
            preset_stop_loss_price=preset_stop_loss_price
        )


async def main():
    async with settings.create_async_exchange() as exchange:
        exchange.set_sandbox_mode(True)
        options = [
            CustomRunnerOptions(
                symbol="SBTCSUSDT",
                timeframe="1m",
                coin_size=0.01,
                runner_class=BTCRunner
            ),
        ]
        rm = RunnerManager(options, exchange, "SUSDT-FUTURES")
        await rm.run()


if __name__ == '__main__':
    asyncio.run(main())
