# -*- coding: utf-8 -*-
from pydantic import BaseModel


class ExchangeApiInfo(BaseModel):
    exchange: str
    api_key: str | None = None
    secret: str | None = None
    password: str | None = None
