# -*- coding: utf-8 -*-
import settings
from bitget.bitget_api import BitgetApi

bitget_api = BitgetApi(
    api_key=settings.bitget_api_key,
    api_secret_key=settings.bitget_api_secret,
    passphrase=settings.bitget_passphrase
)
