# -*- coding: utf-8 -*-
import datetime

from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, ORJSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from conf import settings
from trading.helper import OrderBlockParser, OrderBlockResult
from trading.schema.base import KLine
from web.datastruct import Result

app = FastAPI()

templates = Jinja2Templates(directory="./web/templates")


@app.get("/")
def index(request: Request) -> HTMLResponse:
    context = {
        "request": request,
        "filter": {
            "symbol": "BTCUSDT",
            "timeframe_list": [
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
    timeframe: str
    symbol: str


@app.exception_handler(Exception)
def handle_exception(request: Request, exc: Exception):  # noqa
    return ORJSONResponse(content=Result.failed(str(exc)).model_dump(mode="json"))


@app.post("/get_order_block")
async def get_order_block(param: OrderBlockQuery) -> Result[OrderBlockResult]:
    parser = OrderBlockParser(param.timeframe)
    async with settings.create_async_exchange() as exchange:
        ohlcv = await exchange.fetch_ohlcv(
            symbol=param.symbol,
            timeframe=param.timeframe,
            since=int((datetime.datetime.now() - datetime.timedelta(days=param.day)).timestamp() * 1000),
            params={
                "until": int(datetime.datetime.now().timestamp() * 1000)
            },
            limit=1000
        )
        for data in ohlcv:
            kline = KLine.from_ccxt(data)
            parser.fetch(kline)

    return Result.of(OrderBlockResult(
        order_blocks=list(parser.order_blocks.values()),
        tested_order_blocks=sorted(parser.tested_order_blocks.values(), key=lambda ob_: ob_.start_datetime)
    ))


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=18293,
        use_colors=True
    )
