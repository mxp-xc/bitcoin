# -*- coding: utf-8 -*-
import settings
from libs.bitget.bitget_api import BitgetApi

api = BitgetApi(
    api_key=settings.bitget_api_key,
    api_secret_key=settings.bitget_api_secret,
    passphrase=settings.bitget_passphrase
)

print(api.get(
    "/api/v2/mix/market/history-candles",
    params={
        "symbol": "BTCUSDT",
        "productType": "USDT-FUTURES",
        "granularity": "30m",
        "limit": 200
    }
))
