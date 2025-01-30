# -*- coding: utf-8 -*-
def _patch_requests():
    import requests
    import functools
    __init__ = requests.sessions.Session.__init__

    @functools.wraps(__init__)
    def _init(self: requests.sessions.Session):
        __init__(self)
        self.proxies.update({
            'http': 'http://localhost:7890',
            'https': 'http://localhost:7890',
        })

    requests.sessions.Session.__init__ = _init


_patch_requests()
