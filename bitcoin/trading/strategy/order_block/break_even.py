# -*- coding: utf-8 -*-
import asyncio
import random
from typing import Any, TypedDict, Literal, TYPE_CHECKING, NotRequired

from ccxt.base.exchange import Exchange
from loguru import logger

from bitcoin.conf import settings
from bitcoin.trading.schema.base import KLine
from .listener import KLinePositionListener

if TYPE_CHECKING:
    pass

_break_event_strategy = {}


class BreakEvenStrategyTypedDict(TypedDict):
    strategy: Literal["order_block_price_base", "loss_price_base"]
    timeframe: NotRequired[str | None]
    kwargs: dict[str, Any] | None


class BreakEvenListenerFactory(object):
    def __init__(self, options: BreakEvenStrategyTypedDict):
        strategy = options.get('strategy', None)
        listener_class = _break_event_strategy.get(strategy)
        assert listener_class
        self.timeframe = options.get("timeframe") or "1m"
        self.listener_class = listener_class
        self.init_kwargs = options.get('kwargs') or {}

    def _valida_timeframe(self, exchange: Exchange):
        assert self.timeframe in exchange.options["timeframes"]

    def create_listener(self, *args, **kwargs):
        return self.listener_class(*args, **kwargs, timeframe=self.timeframe, **self.init_kwargs)


class TpslPositionListener(KLinePositionListener):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assert self.order_wrapper.order_info.price

    async def on_open(self):
        await super().on_open()
        asyncio.create_task(self._check_position_exist())

    async def _check_position_exist(self):
        logger.info(f"start check position {self.order_wrapper}")
        retry_count = 0
        try:
            while not self._stopping:
                try:
                    is_exist = await self._position_exist()
                except Exception as exc:
                    # 仓位检测失败超过三次
                    if retry_count > 3:
                        logger.error(f"max retry for {self.order_wrapper}")
                        raise
                    logger.error(f"Failed to check position {self.order_wrapper}", exc)
                    retry_count += 1
                    continue
                # 重置重试次数
                retry_count = 0
                if is_exist:
                    # 1到2分钟检测一次仓位
                    seconds = random.randint(60, 120)
                    logger.info(f"check position exist {self.order_wrapper}. wait {seconds}s next")
                    await asyncio.sleep(seconds)
                else:
                    # 仓位不存在, 停止监听
                    logger.warning(f"position not exist {self.order_wrapper}")
                    break
        except Exception as exc:
            logger.error("Failed to check position exist", exc)
            raise
        finally:
            logger.info(f"stop check position exist {self.order_wrapper}")
            self._stopping = True

    async def _position_exist(self) -> bool:
        positions = await self.runner.exchange.fetch_positions([self.runner.symbol])
        for position in positions:
            if position['side'] == self.order_wrapper.order_block.side:
                return True
        return False

    async def _on_kline(self, klines: list[KLine]):
        for kline in klines:
            # 订单块大小
            break_even = await self._need_break_even(kline)
            if break_even:
                break
        else:
            return
        self._stopping = True

        order_info = self.order_wrapper.order_info
        # 修改止损到开仓价格
        logger.warning(f"订单触发保本条件 {self.order_wrapper.order_block}, 信号k: {kline}")
        orders = await self.runner.exchange.fetch_open_orders(
            self.runner.symbol,
            params={"planType": "profit_loss"}
        )
        for order in orders:
            info = order['info']
            if info['planType'] == "loss_plan":
                logger.warning(f"开始修改订单保本: {order_info}")
                side = "buy" if self.order_wrapper.order_block.side == 'long' else 'sell'
                await self.runner.exchange.edit_order(
                    order['id'], self.runner.symbol, 'market', side, order['amount'],
                    params={
                        "stopLossPrice": order_info.price
                    }
                )
                logger.warning(f"修改保本成功: {order_info}")
                return
        logger.error(f"未找到保本订单: {order_info}")

    async def _need_break_even(self, kline: KLine) -> bool:
        raise NotImplementedError


class _PercentTpslListener(TpslPositionListener):
    def __init__(self, *args, percent: float, **kwargs):
        super().__init__(*args, **kwargs)
        assert percent > 0
        self.percent = percent

    def _start_debug_log(self):
        if not settings.debug:
            return

        async def debug_log():
            while not self._stopping:
                self._log_break_even_price()
                await asyncio.sleep(60)

        asyncio.create_task(debug_log())

    def _log_break_even_price(self):
        pass


class LossPricePercentTpslListener(_PercentTpslListener):
    """基于止损的保本策略, 当止盈到达给定的止损的百分比时候, 就会设置保本止损"""

    def __init__(self, *args, percent: float, **kwargs):
        super().__init__(*args, percent=percent, **kwargs)
        order_info = self.order_wrapper.order_info
        self.loss_price_delta = abs(order_info.price - order_info.preset_stop_loss_price) * self.percent
        self._start_debug_log()

    def _log_break_even_price(self):
        order_info = self.order_wrapper.order_info
        if order_info.side == 'buy':
            break_event_price = order_info.price + self.loss_price_delta
        else:
            break_event_price = order_info.price - self.loss_price_delta
        logger.info(f"监听订单{order_info}. 目标保本价格为: {break_event_price}")

    async def _need_break_even(self, kline: KLine) -> bool:
        order_info = self.order_wrapper.order_info

        if order_info.side == 'buy':
            # 做多, 最高价格距离开仓价格为达到百分比则触发保本
            return kline.highest_price >= order_info.price + self.loss_price_delta
        # 做空, 最低价格距离开仓价格为达到百分比则触发保本
        return kline.lowest_price <= order_info.price - self.loss_price_delta


_break_event_strategy['loss_price_base'] = LossPricePercentTpslListener


class OrderBlockPercentTpslListener(_PercentTpslListener):
    """基于订单块大小的止损的保本策略, 当止盈到达订单块的百分比时候, 就会设置保本止损"""

    def _log_break_even_price(self):
        super()._log_break_even_price()

    async def _need_break_even(self, kline: KLine) -> bool:
        pass
