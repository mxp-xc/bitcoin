import asyncio
import datetime
import uuid
from typing import TYPE_CHECKING, Type, Any

from ccxt.base.errors import ExchangeError, InsufficientFunds
from ccxt.base.types import Position
from ccxt.pro import Exchange
from loguru import logger
from pydantic import BaseModel

from trading import utils
from trading.exceptions import StopTradingException
from trading.helper import OrderBlockParser
from trading.schema.base import OrderBlock, KLine
from .position import PositionStrategyTypedDict, PositionStrategy, create_position_strategy
from .schema import OrderInfo, PlaceOrderContext

if TYPE_CHECKING:
    # for dev
    from ccxt.pro.bitget import bitget as Exchange  # noqa


def _client_oid_default_factory() -> str:
    uid = str(uuid.uuid4()).replace("-", "")
    return f"ob-test-{uid}"


class RunnerOption(BaseModel):
    symbol: str
    timeframe: str
    position_strategy: PositionStrategyTypedDict
    init_kwargs: dict[str, Any] | None = None
    min_fvg_percent: float = 0  # 最小
    min_order_block_kline_undulate_percent: float = 0  # 最小订单块振幅
    max_order_block_kline_undulate_percent: float = float('inf')


class Runner(object):
    def __init__(
        self,
        symbol: str,
        product_type: str,
        exchange: Exchange,
        position_strategy: PositionStrategyTypedDict,
        timeframe: str,
        min_fvg_percent: float,
        min_order_block_kline_undulate_percent: float,
        max_order_block_kline_undulate_percent: float,
        **kwargs
    ):
        if timeframe == '1s':
            raise ValueError("not support 1s timeframe")
        self.timeframe = timeframe
        self.position_strategy: PositionStrategy = create_position_strategy(
            position_strategy.get('strategy'),
            **(position_strategy["kwargs"] or {})
        )
        self.symbol = symbol
        self.product_type = product_type
        self.exchange = exchange
        self.min_fvg_percent = min_fvg_percent
        self.max_order_block_kline_undulate_percent = max_order_block_kline_undulate_percent
        self.min_order_block_kline_undulate_percent = min_order_block_kline_undulate_percent
        self._lock = asyncio.Lock()

    async def run(self):
        while True:
            try:
                await self._run()
            except StopTradingException:
                logger.exception(f"stop trading for {self.__class__.__qualname__} {self.symbol} {self.timeframe}")
                break
            except Exception as exc:  # noqa: ignored
                logger.exception(f"Failed to run for: {self.symbol}, {self.timeframe}")
                await asyncio.sleep(3)

    async def _run_once_with_lock(self):
        async with self._lock:
            await self._run_once()

    async def _run(self):
        await asyncio.gather(*[
            # self._watch_my_trades(),
            self._watch_klines()
        ])

    async def _watch_klines(self):
        """检查k线, 如果收线则重新检查订单块并下单"""
        logger.info("watch klines")
        prepare_task = asyncio.create_task(self.exchange.watch_ohlcv(self.symbol, self.timeframe))
        last_run_datetime = datetime.datetime.now()
        await self._run_once_with_lock()
        await prepare_task

        while True:
            ohlcv = await self.exchange.watch_ohlcv(self.symbol, self.timeframe)
            kline = KLine.from_ccxt(ohlcv[-1])
            if kline.opening_time > last_run_datetime:
                logger.info(
                    f"close kline: {utils.format_datetime(last_run_datetime)} -> "
                    f"{utils.format_datetime(kline.opening_time)}"
                )
                logger.info("_run_once_with_lock by _watch_klines")
                await self._sleep_interval()  # 延迟一段时间, 防止bg接口没有最新数据
                await self._run_once_with_lock()
                last_run_datetime = kline.opening_time

    async def _sleep_interval(self):
        second = self.exchange.parse_timeframe(self.timeframe)
        # 1/30的间隔
        time_to_wait = min(second / 30, 60)
        logger.info(f"sleep: {time_to_wait}s")
        await asyncio.sleep(time_to_wait)

    async def _watch_my_trades(self):
        """监听订单成交, 如果出现成交则重新检查订单块下单"""
        logger.info("watch my trades")
        while True:
            await self.exchange.watch_my_trades(self.symbol)
            logger.info("_run_once_with_lock by _watch_my_trades")
            await self._run_once_with_lock()

    async def _get_klines(self, since: int | None = None, until: int | None = None) -> list[KLine]:
        since = since or int((datetime.datetime.now() - datetime.timedelta(days=60)).timestamp() * 1000)
        until = until or int(datetime.datetime.now().timestamp() * 1000)
        ohlcv = await self.exchange.fetch_ohlcv(
            symbol=self.symbol,
            timeframe=self.timeframe,
            since=since,
            params={
                "until": until
            },
            limit=1000
        )
        return [KLine.from_ccxt(d) for d in ohlcv]

    async def _init_ob_parser(self):
        ob_parser = OrderBlockParser(timeframe=self.timeframe)
        logger.info(f"读取`{self.symbol}`k线分析订单块中, 时间级别为: {self.timeframe}")
        klines = await self._get_klines()
        if not klines:
            raise StopTradingException("not klines")
        logger.info(
            f"读取到的时间范围"
            f"{utils.format_datetime(klines[0].opening_time)} - {utils.format_datetime(klines[-1].opening_time)}"
        )
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
        raise NotImplementedError

    async def _post_process_order_info(
        self,
        order_block: OrderBlock,
        context: PlaceOrderContext,
        order_info: OrderInfo
    ) -> OrderInfo:
        coin_size = await self._get_position_amount(order_info)
        order_info.amount = coin_size
        return order_info

    async def _get_position_amount(self, order_info: OrderInfo):
        try:
            coin_size = await self.position_strategy.get_amount(order_info, self)
            if coin_size <= 0:
                raise StopTradingException("配置的仓位策略下单<=0个")
            if self.position_strategy.leverage:
                return coin_size * self.position_strategy.leverage
            if self.exchange.id != "bitget":
                raise StopTradingException("非bitget平台不支持动态读取杠杆计算仓位")
            value = await self.exchange.fetch_leverage(self.symbol)
            leverage = value['info']['crossedMarginLeverage']
            return coin_size * leverage

        except StopTradingException:
            raise
        except Exception as exc:
            raise StopTradingException("计算仓位出错") from exc

    async def _create_order0(self, order_info: OrderInfo):
        """下单"""
        if not order_info.amount or order_info.amount < 0:
            raise StopTradingException(f"invalid order_info: {order_info}")
        params = {"tradeSide": "open"}
        if order_info.preset_stop_surplus_price:
            params["takeProfit"] = {"stopPrice": order_info.preset_stop_surplus_price}
        else:
            raise StopTradingException("订单必须携带止盈")
        if order_info.preset_stop_loss_price:
            params["stopLoss"] = {"stopPrice": order_info.preset_stop_loss_price}
        else:
            raise StopTradingException("订单必须携带止损")
        try:
            return await self.exchange.create_limit_order(
                self.symbol,
                order_info.side,
                order_info.amount,
                price=order_info.price,
                params=params
            )
        except InsufficientFunds as exc:
            raise StopTradingException("余额不足") from exc
        except ExchangeError as exc:
            raise StopTradingException("创建订单失败") from exc

    async def _create_order(
        self,
        order_blocks: list[OrderBlock],
        mutex_order_blocs: list[OrderBlock],
        klines: list[KLine]
    ):
        context = PlaceOrderContext(
            order_blocks=order_blocks,
            mutex_order_blocs=mutex_order_blocs,
            current_kline=klines[-1]
        )
        order_block = await self._choice_order_block(order_blocks, context)
        if not order_block:
            return

        order_info = await self._resolve_order_info(order_block, context)
        order_info = await self._post_process_order_info(order_block, context, order_info)
        await self._process_mutex_order_locks(context.mutex_order_blocs, context, order_info)
        logger.info(f"{order_info = }. {order_block.order_block_kline}")
        await self._create_order0(order_info)

    async def _process_mutex_order_locks(
        self,
        mutex_order_blocks: list[OrderBlock],
        context: PlaceOrderContext,
        order_info: OrderInfo
    ):
        logger.info("process mutex order blocks")
        order_block = await self._choice_order_block(mutex_order_blocks, context)
        if not order_block:
            return
        if order_info.side == 'buy':
            # 做多, 止盈价格需要小于空订单块入场点
            new_preset_stop_surplus_price = min(
                order_info.preset_stop_surplus_price, order_block.order_block_kline.lowest_price
            )
        else:
            # 做空, 止盈价格需要小于空订单块入场点
            new_preset_stop_surplus_price = max(
                order_info.preset_stop_surplus_price, order_block.order_block_kline.highest_price
            )

        if new_preset_stop_surplus_price != order_info.preset_stop_surplus_price:
            logger.warning(
                f"modify order preset_stop_surplus_price: "
                f"{order_info.preset_stop_surplus_price} -> {new_preset_stop_surplus_price}."
            )
            order_info.preset_stop_surplus_price = new_preset_stop_surplus_price

    async def _choice_order_block(
        self,
        order_blocks: list[OrderBlock],
        context: PlaceOrderContext,
    ) -> OrderBlock | None:  # noqa
        if not context.order_blocks:
            return None
        for order_block in order_blocks:
            message = []
            order_block_kline = order_block.order_block_kline
            fvg_list = order_block.get_fvg_percent()
            if not fvg_list:
                raise StopTradingException(f"fvg not found. {order_block}")
            if max(fvg_list) < self.min_fvg_percent:
                message.append(f"[fvg: reject] max fvg: {max(fvg_list)} < {self.min_fvg_percent}. {fvg_list}")

            undulate = order_block_kline.get_undulate_percent(
                side=order_block.side  # noqa
            )
            if (
                undulate < self.min_order_block_kline_undulate_percent
                or undulate > self.max_order_block_kline_undulate_percent
            ):
                min_undulate = self.min_order_block_kline_undulate_percent
                max_undulate = self.max_order_block_kline_undulate_percent
                message.append(
                    f"[振幅] "
                    f"{undulate} not in [{min_undulate} - {max_undulate}]"
                )

            extra_message = await self._choice_order_block_extra(order_block, context)
            message.extend(extra_message)
            if not message:
                return order_block
            logger.info(
                f"[{self.__class__.__qualname__}]-{self.symbol}-{self.timeframe} reject] "
                f"{order_block_kline}\n"
                f"{'\n'.join(message)}"
            )
        return None

    async def _choice_order_block_extra(
        self,
        order_block: OrderBlock,
        context: PlaceOrderContext,
    ) -> list[str]:
        return []

    def _parse_order_block(self, order_block):
        pass

    async def _run_once(self):
        # 当前持仓数据
        logger.info(f"run once for {self.symbol}, {self.timeframe = }")
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
        await self._cancel_all_orders()
        if 'long' not in position_map and long_order_blocks:
            # 存在多单订单块并且没有多单持仓, 才下订单
            await self._create_order(long_order_blocks, short_order_blocks, klines)
        elif long_order_blocks:
            logger.info(f"当前仓位存在多单")

        if 'short' not in position_map and short_order_blocks:
            # 存在空单订单块并且没有多单持仓, 才下订单
            await self._create_order(short_order_blocks, long_order_blocks, klines)
        elif short_order_blocks:
            logger.info(f"当前仓位存在空单")


class EntryRunner(Runner):
    def __init__(self, middle_entry_undulate: float = float('inf'), **kwargs):
        self.middle_entry_undulate = middle_entry_undulate
        super().__init__(**kwargs)

    async def _resolve_order_info(
        self,
        order_block: OrderBlock,
        context: PlaceOrderContext
    ) -> OrderInfo:
        kline = order_block.order_block_kline
        if order_block.side == 'long':
            # 多单, 除下影线
            undulate = (kline.delta_price / kline.lowest_price) * 100
        else:
            # 空单, 除上影线
            undulate = (kline.delta_price / kline.highest_price) * 100
        # assert 0.4 < undulate < 1.5
        if undulate > self.middle_entry_undulate:
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
        else:
            # 做空止损上影线
            preset_stop_loss_price = kline.highest_price

        return OrderInfo(
            side='buy' if order_block.side == 'long' else 'sell',
            price=price,
            preset_stop_loss_price=preset_stop_loss_price
        )


class CustomRunnerOptions(RunnerOption):
    runner_class: Type[Runner]
