# -*- coding: utf-8 -*-
from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, ORJSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from helper import OrderBlockResult, get_klines, OrderBlockParser
from web.datastruct import Result

app = FastAPI()

templates = Jinja2Templates(directory="./web/templates")


@app.get("/")
def index(request: Request) -> HTMLResponse:
    context = {
        "request": request,
        "filter": {
            "symbol": "BTCUSDT",
            "granularity_list": [
                {
                    "label": "30分",
                    "value": "30m",
                    "selected": True
                },
                {
                    "label": "1小时",
                    "value": "1H"
                },
                {
                    "label": "4小时",
                    "value": "4H"
                }
            ],
            "day_list": [
                {
                    "label": "1天内",
                    "value": "1",
                    "selected": True
                },
                {
                    "label": "3天内",
                    "value": "3"
                },
                {
                    "label": "默认",
                    "value": "60"
                }
            ]
        }
    }
    return templates.TemplateResponse(
        "index.html", context=context
    )


class OrderBlockQuery(BaseModel):
    day: int
    granularity: str
    symbol: str


@app.exception_handler(Exception)
def handle_exception(request: Request, exc: Exception):  # noqa
    return ORJSONResponse(content=Result.failed(str(exc)).model_dump(mode="json"))


@app.post("/get_order_block")
def get_order_block(param: OrderBlockQuery) -> Result[OrderBlockResult]:
    parser = OrderBlockParser()
    for kline in get_klines(
            symbol=param.symbol,
            granularity=param.granularity,
            day=param.day
    )[:-1]:
        parser.fetch(kline)

    return Result.of(OrderBlockResult(
        order_blocks=list(parser.order_blocks.values()),
        tested_order_blocks=sorted(parser.tested_order_blocks, key=lambda ob_: ob_.start_datetime)
    ))
