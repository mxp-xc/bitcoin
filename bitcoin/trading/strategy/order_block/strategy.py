from ccxt.async_support import Exchange
from pydantic import BaseModel

from bitcoin.trading.helper import KLineWatcher, KLineWrapper  # noqa


class StrategyOptions(BaseModel):
    pass


class BaseStrategy(object):
    def __init__(
        self,
        symbol: str,
        timeframe: str,
        product_type: str,
        exchange: Exchange | None = None,
        options: StrategyOptions | None = None,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self.product_type = product_type
        self.options = options
        self.exchange = exchange or self.create_exchange()
        self._stopping = False

    def create_exchange(self) -> Exchange:
        raise NotImplementedError

    async def run(self):
        while not self._stopping:
            pass

    async def _run(self):
        pass

    def stop(self):
        self._stopping = True

    def is_stop(self):
        pass
