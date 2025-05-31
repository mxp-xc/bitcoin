# -*- coding: utf-8 -*-
from pydantic import BaseModel


class ExchangeApiInfo(BaseModel):
    exchange: str
    api_key: str | None = None
    secret: str | None = None
    password: str | None = None


class ExchangeProxy(BaseModel):
    host: str
    port: int


class ExchangeConfiguration(BaseModel):
    proxy: ExchangeProxy | None
    api_info: dict[str, ExchangeApiInfo]

    def get_api_info(self, name: str = "default"):
        return self.api_info[name]

    def get_proxy_http_base_url(self, schema: str = "http") -> str | None:
        if not self.proxy:
            return None
        return f"{schema}://{self.proxy.host}:{self.proxy.port}"
