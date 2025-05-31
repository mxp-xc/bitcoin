# -*- coding: utf-8 -*-
from typing import Any

from ccxt import async_support as async_ccxt
from pydantic import BaseModel

DEFAULT_API_INFO_NAME = "default"
type API_INFO_TYPE = str | "ExchangeApiInfo" | None


class ExchangeApiInfo(BaseModel):
    exchange: str
    api_key: str | None = None
    secret: str | None = None
    password: str | None = None


class ExchangeProxy(BaseModel):
    host: str
    port: int


class ExchangeConfiguration(BaseModel):
    proxy: ExchangeProxy | None = None
    api_info: dict[str, ExchangeApiInfo]

    def get_api_info(self, name: str = DEFAULT_API_INFO_NAME):
        return self.api_info[name]

    def get_proxy_http_base_url(self, schema: str = "http") -> str | None:
        if not self.proxy:
            return None
        return f"{schema}://{self.proxy.host}:{self.proxy.port}"

    def create_async_exchange(self, api_info: API_INFO_TYPE = None, **kwargs) -> async_ccxt.Exchange:
        return self.create_ccxt_exchange(async_ccxt, api_info=api_info, **kwargs)

    def create_async_exchange_public(self, exchange: str, **kwargs) -> async_ccxt.Exchange:
        return self.create_ccxt_exchange(async_ccxt, api_info=ExchangeApiInfo(exchange=exchange), **kwargs)

    def create_ccxt_exchange(self, module, api_info: API_INFO_TYPE = None, **kwargs):
        if api_info is None or isinstance(api_info, str):
            api_info = self.get_api_info(api_info or DEFAULT_API_INFO_NAME)
        assert isinstance(api_info, ExchangeApiInfo)
        assert api_info.exchange in module.exchanges, f"不支持的交易商: {api_info.exchange}"
        kws: dict[str, Any] = {"options": {"defaultType": "swap", "maxRetriesOnFailure": 3}}
        if api_info.exchange == "bitget":
            # 令bitget支持监听1s的频道
            kws["options"]["timeframes"] = {"1s": "1s"}

        if api_info.api_key:
            assert api_info.secret and api_info.password
            kws["apiKey"] = api_info.api_key
            kws["secret"] = api_info.secret
            kws["password"] = api_info.password

        proxy = self.get_proxy_http_base_url()
        if proxy:
            kws["https_proxy"] = proxy
            kws["ws_proxy"] = proxy

        kws.update(kwargs)
        exchange = getattr(module, api_info.exchange)(kws)
        return exchange
