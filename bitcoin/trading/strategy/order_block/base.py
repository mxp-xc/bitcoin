import asyncio
import datetime
import uuid
from typing import TYPE_CHECKING, Any, Type

from ccxt.base.errors import ExchangeError, InsufficientFunds
from ccxt.base.types import Order, Position, PositionSide
from ccxt.pro import Exchange
from loguru import logger
from pydantic import BaseModel

from bitcoin import utils
from bitcoin.trading.exceptions import StopTradingException
from bitcoin.trading.helper import OrderBlockParser
from bitcoin.trading.schema.base import KLine, OrderBlock

from .break_even import BreakEvenListenerFactory, BreakEvenStrategyTypedDict
from .listener import PositionWrapper
from .position import (
    PositionStrategy,
    PositionStrategyTypedDict,
    create_position_strategy,
)
from .schema import OrderInfo, PlaceOrderContext, PlaceOrderWrapper

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
    break_even_strategy: BreakEvenStrategyTypedDict | None = None
    init_kwargs: dict[str, Any] | None = None
    first_min_fvg_percent: float = 0  # 第一个fvg最小需要满足的条件
    min_fvg_percent: float = 0  # 最小
    min_order_block_kline_undulate_percent: float = 0  # 最小订单块振幅
    max_order_block_kline_undulate_percent: float = float("inf")


class Runner(object):
    break_even_listener_factory: BreakEvenListenerFactory | None

    def __init__(
        self,
        symbol: str,
        product_type: str,
        exchange: Exchange,
        position_strategy: PositionStrategyTypedDict,
        timeframe: str,
        min_order_block_kline_undulate_percent: float,
        max_order_block_kline_undulate_percent: float,
        first_min_fvg_percent: float = float("-inf"),
        min_fvg_percent: float = float("-inf"),
        break_even_strategy: BreakEvenStrategyTypedDict | None = None,
        **kwargs,
    ):
        if timeframe == "1s":
            raise ValueError("not support 1s timeframe")
        self.exchange = exchange
        self.timeframe = timeframe
        self.position_strategy: PositionStrategy = create_position_strategy(
            position_strategy.get("strategy"),
            **(position_strategy["kwargs"] or {}),
        )
        if break_even_strategy:
            self.break_even_listener_factory = BreakEvenListenerFactory(
                break_even_strategy
            )
            self.break_even_listener_factory._valida_timeframe(exchange)  # noqa
        else:
            self.break_even_listener_factory = None
        self.symbol = symbol
        self.product_type = product_type
        self.min_fvg_percent = min_fvg_percent
        self.first_min_fvg_percent = first_min_fvg_percent
        self.max_order_block_kline_undulate_percent = (
            max_order_block_kline_undulate_percent
        )
        self.min_order_block_kline_undulate_percent = (
            min_order_block_kline_undulate_percent
        )
        self._stopping = False
        self._pending_order = {}
        self._position_order = {}
        self._lock = asyncio.Lock()

    async def run(self):
        while not self._stopping:
            try:
                await self._run()
            except (StopTradingException, AssertionError):
                logger.exception(
                    f"stop trading for {self.__class__.__qualname__} {self.symbol} {self.timeframe}"
                )
                break
            except Exception:  # noqa: ignored
                logger.exception(
                    f"Failed to run for: {self.symbol}, {self.timeframe}"
                )
                await asyncio.sleep(3)

    async def _run_once_with_lock(self):
        async with self._lock:
            await self._run_once()

    async def _run(self):
        pre_task = asyncio.gather(
            *[
                self._watch_orders(),
                self._watch_positions(),
            ]
        )
        await asyncio.sleep(3)
        await asyncio.gather(*[pre_task, self._watch_klines()])

    async def _watch_klines(self):
        """检查k线, 如果收线则重新检查订单块并下单"""
        logger.info("watch klines")
        prepare_task = asyncio.create_task(
            self.exchange.watch_ohlcv(self.symbol, self.timeframe)
        )
        last_run_datetime = datetime.datetime.now()
        await self._run_once_with_lock()
        await prepare_task

        while not self._stopping:
            ohlcv_list = await self.exchange.watch_ohlcv(
                self.symbol, self.timeframe
            )
            # 判断止盈止损
            klines = [KLine.from_ccxt(ohlcv) for ohlcv in ohlcv_list]
            last_kline = klines[-1]
            if last_kline.opening_time > last_run_datetime:
                logger.warning(
                    f"close kline: {utils.format_datetime(last_run_datetime)} -> "
                    f"{utils.format_datetime(last_kline.opening_time)}"
                )
                await self._on_kline_close("_watch_klines")
                last_run_datetime = last_kline.opening_time

    async def _on_kline_close(self, source):
        logger.info(f"_wake_up_run_once by {source}")
        await self._sleep_interval()  # 延迟一段时间, 防止bg接口没有最新数据
        await self._run_once_with_lock()

    async def _sleep_interval(self):
        # 等待2分钟再调用接口获取k线
        time_to_wait = 60 * 2
        logger.info(f"sleep: {time_to_wait}s")
        await asyncio.sleep(time_to_wait)

    async def _watch_positions(self):
        """监听平仓事件, 当平仓的时候, 把position_order移除"""
        logger.info("start _watch_positions")
        while not self._stopping:
            positions = await self.exchange.watch_positions([self.symbol])
            for position in positions:
                info = position["info"]
                identity = f"{position['symbol']}-{position['side']}"
                logger.info(f"{identity} position change")
                if not info["available"] == "0":
                    logger.info("ignored position change")
                    continue
                logger.info(f"close position: {identity}")
                await self._remove_position_order(position["side"])

        logger.info("stop _watch_positions")

    async def _watch_orders(self):
        """监听订单成交, 如果出现成交则重新检查订单块下单"""
        logger.info("start watch orders")
        while not self._stopping:
            orders = await self.exchange.watch_orders(self.symbol)
            try:
                await self._update_order(orders)
            except Exception as exc:
                raise StopTradingException("记录订单信息失败") from exc
        logger.info("stop watch orders")

    async def _add_position_order(
        self,
        side: PositionSide,
        order_wrapper: PlaceOrderWrapper,
        position: Position,
    ):
        logger.info(f"add position order: {side}. {order_wrapper.order_info}")
        wrapper = PositionWrapper(
            order_wrapper=order_wrapper,
            listeners=self._create_position_listeners(order_wrapper, position),
        )
        self._position_order[side] = wrapper
        await asyncio.gather(
            *(listener.on_open() for listener in wrapper.listeners)
        )

    async def _remove_position_order(self, side: PositionSide):
        wrapper: PositionWrapper | None = self._position_order.pop(side, None)
        logger.info(f"remove position order: {side}, {wrapper}")
        if wrapper and wrapper.listeners:
            await asyncio.gather(
                *(listener.on_close() for listener in wrapper.listeners)
            )

    def _create_position_listeners(
        self, order_wrapper: PlaceOrderWrapper, position: Position
    ):
        if self.break_even_listener_factory is None:
            return []
        break_even_listener = self.break_even_listener_factory.create_listener(
            runner=self, order_wrapper=order_wrapper, position=position
        )
        return [break_even_listener]

    async def _update_order(self, orders: list[Order]):
        for order in orders:
            if order["symbol"] != self.symbol:
                logger.error(
                    f"invalid symbol. {order['symbol']} != {self.symbol}"
                )
                continue
            client_order_id = order["clientOrderId"]
            if (
                client_order_id not in self._position_order
                and client_order_id not in self._pending_order
            ):
                logger.info(f"ignore order: {client_order_id}")
                continue

            info = order["info"]
            trade_side = info.get("tradeSide", None)
            status = info.get("status", None)
            pos_side = info.get("posSide", None)
            side = info.get("side", None)
            logger.info(
                f"receive order: {client_order_id = }, {trade_side = }, {status = }, {pos_side = }, {side = }"
            )

            if trade_side == "open":
                # 开仓
                if status == "live" and client_order_id in self._pending_order:
                    logger.info(f"live order: {client_order_id}")
                elif status == "canceled":
                    self._pending_order.pop(client_order_id, None)
                    logger.info(f"canceled {client_order_id}")
                elif status in ("filled", "partially_filled"):
                    # 全部成交或者部分成交只直接监听. 后续如果部分成交继续成交了, 会被监听器继续处理. 如果没有成交
                    # 或者监听器关闭了, 也只是无保本. 不会出现乱改止损的情况
                    logger.info(f"{status} {client_order_id}")
                    ow = self._pending_order.pop(client_order_id)
                    assert ow and pos_side
                    await self._process_open_position(pos_side, ow)
                else:
                    logger.error(f"unsupported state: {status}")
                    self._pending_order.pop(client_order_id, None)
                    assert pos_side
                    await self._remove_position_order(pos_side)
            elif trade_side == "close":
                # 平仓
                logger.info(f"closed position order: {client_order_id}")
            else:
                logger.info(
                    f"order: {order['id']} trade size: {trade_side} not in ('open' or 'close')"
                )

    async def _process_open_position(
        self, pos_side: str, ow: PlaceOrderWrapper
    ):
        # 获取仓位信息
        positions = await self.exchange.fetch_positions([self.symbol])
        for position in positions:
            if position["info"]["holdSide"] != pos_side:
                continue

            await self._add_position_order(pos_side, ow, position)
            return
        logger.warning(f"position not found: {ow}")

    async def _get_klines(
        self, since: int | None = None, until: int | None = None
    ) -> list[KLine]:
        since = since or int(
            (datetime.datetime.now() - datetime.timedelta(days=60)).timestamp()
            * 1000
        )
        until = until or int(datetime.datetime.now().timestamp() * 1000)
        ohlcv = await self.exchange.fetch_ohlcv(
            symbol=self.symbol,
            timeframe=self.timeframe,
            since=since,
            params={"until": until},
            limit=1000,
        )
        return [KLine.from_ccxt(d) for d in ohlcv]

    async def _init_ob_parser(self):
        ob_parser = OrderBlockParser(timeframe=self.timeframe)
        logger.info(
            f"读取`{self.symbol}`k线分析订单块中, 时间级别为: {self.timeframe}"
        )
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
        return {position["side"]: position for position in positions}

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

    async def _resolve_order_info(
        self, order_block: OrderBlock, context: PlaceOrderContext
    ) -> OrderInfo:
        raise NotImplementedError

    async def _post_process_order_info(
        self,
        order_block: OrderBlock,
        context: PlaceOrderContext,
        order_info: OrderInfo,
    ) -> OrderInfo:
        coin_size = await self._get_position_amount(order_info)
        order_info.amount = coin_size
        return order_info

    async def _get_position_amount(self, order_info: OrderInfo):
        try:
            coin_size = await self.position_strategy.get_amount(
                order_info, self
            )
            if coin_size <= 0:
                raise StopTradingException("配置的仓位策略下单<=0个")
            if self.position_strategy.leverage:
                return coin_size * self.position_strategy.leverage
            if self.exchange.id != "bitget":
                raise StopTradingException(
                    "非bitget平台不支持动态读取杠杆计算仓位"
                )
            value = await self.exchange.fetch_leverage(self.symbol)
            leverage = value["info"]["crossedMarginLeverage"]
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
            params["takeProfit"] = {
                "stopPrice": order_info.preset_stop_surplus_price
            }
        else:
            raise StopTradingException("订单必须携带止盈")
        if order_info.preset_stop_loss_price:
            params["stopLoss"] = {
                "stopPrice": order_info.preset_stop_loss_price
            }
        else:
            raise StopTradingException("订单必须携带止损")
        if order_info.client_order_id:
            params["clientOrderId"] = order_info.client_order_id
        else:
            raise StopTradingException("没有自定义order_id")

        try:
            return await self.exchange.create_limit_order(
                self.symbol,
                order_info.side,
                order_info.amount,
                price=order_info.price,
                params=params,
            )
        except InsufficientFunds as exc:
            raise StopTradingException("余额不足") from exc
        except ExchangeError as exc:
            raise StopTradingException("创建订单失败") from exc

    async def _create_order(
        self,
        order_blocks: list[OrderBlock],
        mutex_order_blocs: list[OrderBlock],
        klines: list[KLine],
    ):
        context = PlaceOrderContext(
            order_blocks=order_blocks,
            mutex_order_blocs=mutex_order_blocs,
            current_kline=klines[-1],
        )
        order_block = await self._choice_order_block(order_blocks, context)
        if not order_block:
            return

        order_info = await self._resolve_order_info(order_block, context)
        order_info.client_order_id = _client_oid_default_factory()
        order_info = await self._post_process_order_info(
            order_block, context, order_info
        )
        await self._process_mutex_order_locks(
            context.mutex_order_blocs, context, order_info
        )
        logger.info(f"{order_info = }. {order_block.order_block_kline}")

        if not order_info.client_order_id:
            raise StopTradingException("client order id is None")
        self._pending_order[order_info.client_order_id] = PlaceOrderWrapper(
            order_block=order_block, order_info=order_info
        )
        await self._create_order0(order_info)

    async def _process_mutex_order_locks(
        self,
        mutex_order_blocks: list[OrderBlock],
        context: PlaceOrderContext,
        order_info: OrderInfo,
    ):
        logger.info("process mutex order blocks")
        order_block = await self._choice_order_block(
            mutex_order_blocks, context
        )
        if not order_block:
            return
        if order_info.side == "buy":
            # 做多, 止盈价格需要小于空订单块入场点
            new_preset_stop_surplus_price = min(
                order_info.preset_stop_surplus_price,
                order_block.order_block_kline.lowest_price,
            )
        else:
            # 做空, 止盈价格需要小于空订单块入场点
            new_preset_stop_surplus_price = max(
                order_info.preset_stop_surplus_price,
                order_block.order_block_kline.highest_price,
            )

        if (
            new_preset_stop_surplus_price
            != order_info.preset_stop_surplus_price
        ):
            logger.warning(
                f"modify order preset_stop_surplus_price: "
                f"{order_info.preset_stop_surplus_price} -> {new_preset_stop_surplus_price}."
            )
            order_info.preset_stop_surplus_price = (
                new_preset_stop_surplus_price
            )

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
            if fvg_list[0] < self.first_min_fvg_percent:
                message.append(
                    f"[fvg: reject] first fvg: {fvg_list[0]} < {self.first_min_fvg_percent}. {fvg_list}"
                )
            elif max(fvg_list) < self.min_fvg_percent:
                message.append(
                    f"[fvg: reject] max fvg: {max(fvg_list)} < {self.min_fvg_percent}. {fvg_list}"
                )

            undulate = order_block_kline.get_undulate_percent(
                side=order_block.side
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

            extra_message = await self._choice_order_block_extra(
                order_block, context
            )
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
        if "short" in position_map and "long" in position_map:
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
            (
                ob
                for ob in ob_parser.order_blocks.values()
                if ob.side == "long"
            ),
            key=lambda o: o.order_block_kline.highest_price,
            reverse=True,
        )
        short_order_blocks = sorted(
            (
                ob
                for ob in ob_parser.order_blocks.values()
                if ob.side == "short"
            ),
            key=lambda o: o.order_block_kline.lowest_price,
        )
        await self._cancel_all_orders()
        if "long" not in position_map and long_order_blocks:
            # 存在多单订单块并且没有多单持仓, 才下订单
            logger.info("start place long order")
            await self._create_order(
                long_order_blocks, short_order_blocks, klines
            )
        elif long_order_blocks:
            logger.info("当前仓位存在多单")

        if "short" not in position_map and short_order_blocks:
            # 存在空单订单块并且没有多单持仓, 才下订单
            logger.info("start place short order")
            await self._create_order(
                short_order_blocks, long_order_blocks, klines
            )
        elif short_order_blocks:
            logger.info("当前仓位存在空单")


class EntryRunner(Runner):
    def __init__(self, middle_entry_undulate: float = float("inf"), **kwargs):
        self.middle_entry_undulate = middle_entry_undulate
        super().__init__(**kwargs)

    async def _resolve_order_info(
        self, order_block: OrderBlock, context: PlaceOrderContext
    ) -> OrderInfo:
        kline = order_block.order_block_kline
        if order_block.side == "long":
            # 多单, 除下影线
            undulate = (kline.delta_price / kline.lowest_price) * 100
        else:
            # 空单, 除上影线
            undulate = (kline.delta_price / kline.highest_price) * 100
        # assert 0.4 < undulate < 1.5
        if undulate > self.middle_entry_undulate:
            # 中轨
            price = kline.center_price
        elif order_block.side == "long":
            # 多上影线
            price = kline.highest_price
        else:
            # 空上影线
            price = kline.lowest_price

        if order_block.side == "long":
            # 做多止损下影线
            preset_stop_loss_price = kline.lowest_price
            preset_stop_surplus_price = price + abs(
                price - preset_stop_loss_price
            )
        else:
            # 做空止损上影线
            preset_stop_loss_price = kline.highest_price
            preset_stop_surplus_price = price - abs(
                price - preset_stop_loss_price
            )

        # 止盈一个点
        return OrderInfo(
            side="buy" if order_block.side == "long" else "sell",
            price=price,
            preset_stop_loss_price=preset_stop_loss_price,
            preset_stop_surplus_price=preset_stop_surplus_price,
        )


class CustomRunnerOptions(RunnerOption):
    runner_class: Type[Runner]
