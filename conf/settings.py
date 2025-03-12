# -*- coding: utf-8 -*-
from functools import cached_property

import ccxt as sync_ccxt
from ccxt import pro as async_ccxt
from loguru import logger
from pydantic import BaseModel

from .schema import ExchangeApiInfo


class _Settings(BaseModel):
    proxy_http_host: str | None = None

    proxy_http_port: int | None = None

    def get_proxy_http_base_url(self, schema: str = "http") -> str | None:
        if not self.proxy_http_port and not self.proxy_http_host:
            return None
        assert self.proxy_http_host and self.proxy_http_port
        return f"{schema}://{self.proxy_http_host}:{self.proxy_http_port}"

    @cached_property
    def api_info(self) -> ExchangeApiInfo:
        from ._settings import api_info as default_api_info
        return default_api_info

    def create_sync_exchange(
        self,
        api_info: ExchangeApiInfo | None = None,
        **kwargs
    ) -> sync_ccxt.Exchange:
        return self._create_exchange(sync_ccxt, api_info, **kwargs)

    def create_async_exchange(
        self,
        api_info: ExchangeApiInfo | None = None,
        **kwargs
    ) -> async_ccxt.Exchange:
        return self._create_exchange(async_ccxt, api_info, **kwargs)

    def _create_exchange(
        self,
        module,
        api_info: ExchangeApiInfo | None = None,
        **kwargs
    ):
        api_info = api_info or self.api_info
        assert api_info.exchange in module.exchanges, f"不支持的交易商: {api_info.exchange}"

        kws = {
            "apiKey": api_info.api_key,
            "secret": api_info.secret,
            "password": api_info.password,
            "options": {
                "defaultType": "swap",
                "maxRetriesOnFailure": 3
            }
        }
        proxy = self.get_proxy_http_base_url()
        if proxy:
            kws["https_proxy"] = proxy
            kws["ws_proxy"] = proxy

        kws.update(kwargs)
        return getattr(module, api_info.exchange)(kws)


settings = _Settings()


def __log_settings():
    settings.api_info  # noqa: for dev
    proxy = settings.get_proxy_http_base_url()
    if proxy:
        logger.info(f"使用代理: {proxy}")
    else:
        logger.info("未使用代理")


__log_settings()
del __log_settings
