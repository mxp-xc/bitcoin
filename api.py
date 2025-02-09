# -*- coding: utf-8 -*-
from functools import cached_property

import settings


class BitgetApi(object):
    def __init__(self, *args, **kwargs):
        self._args = args
        self._kwargs = kwargs

    @cached_property
    def api(self):
        from bitget.bitget_api import BitgetApi as _BitgetApi

        return _BitgetApi(*self._args, **self._kwargs)

    @cached_property
    def order_api(self):
        from bitget.v2.mix.order_api import OrderApi
        return OrderApi(*self._args, **self._kwargs)

    @cached_property
    def account_api(self):
        from bitget.v2.mix.account_api import AccountApi
        return AccountApi(*self._args, **self._kwargs)


bitget_api = BitgetApi(
    api_key=settings.bitget_api_key,
    api_secret_key=settings.bitget_api_secret,
    passphrase=settings.bitget_passphrase
)
