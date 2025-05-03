import abc
import asyncio

from ccxt.async_support import Exchange

from bitcoin.trading.helper import KLineWatcher
from bitcoin.utils import log_and_send_wx_message


class BaseWatcher(object):
    def __init__(
        self,
        kline_watcher: KLineWatcher,
        symbol: str,
        timeframe: str,
        wx_key: str | None = None,
    ):
        self.kline_watcher = kline_watcher
        self.symbol = symbol
        self.timeframe = timeframe
        self.wx_key = wx_key

    @abc.abstractmethod
    async def watch(self):
        raise NotImplementedError


class WatcherManager(object):
    def __init__(
        self,
        symbols: list[str],
        timeframes: list[str],
        wx_key: str | None = None,
    ):
        self.symbols = symbols
        self.timeframes = timeframes
        self.wx_key = wx_key

    def create_exchange(self) -> Exchange:
        raise NotImplementedError

    def create_watcher(
        self,
        kline_watcher: KLineWatcher,
        symbol: str,
        timeframe: str,
        wx_key: str | None = None,
        **kwargs,
    ):
        raise NotImplementedError

    async def watch(self):
        watchers = []
        async with self.create_exchange() as exchange:
            kline_watcher = KLineWatcher(exchange)
            await exchange.load_markets()
            for symbol in self.symbols:
                exchange.market(symbol)

                for timeframe in self.timeframes:
                    watcher = self.create_watcher(
                        kline_watcher=kline_watcher,
                        symbol=symbol,
                        timeframe=timeframe,
                        wx_key=self.wx_key,
                    )
                    watchers.append(watcher)
        symbol_time_frames = (
            f"{watcher.symbol} - {watcher.timeframe}" for watcher in watchers
        )
        message = f"collect watcher:\n{'\n'.join(symbol_time_frames)}"
        await log_and_send_wx_message(message, key=self.wx_key)
        await asyncio.gather(*(watcher.watch() for watcher in watchers))
