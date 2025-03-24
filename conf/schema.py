# -*- coding: utf-8 -*-
from pydantic import BaseModel


class ExchangeApiInfo(BaseModel):
    exchange: str
    api_key: str
    secret: str
    password: str | None = None
