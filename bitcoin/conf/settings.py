# -*- coding: utf-8 -*-
import datetime
import sys
from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo

import ccxt as sync_ccxt
from ccxt import pro as async_ccxt
from dotenv import load_dotenv
from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict

from .schema import ExchangeApiInfo

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_project_path: Path = Path(__file__).parent.parent.parent.resolve()
_dot_env_path = _project_path / ".env"


class _Settings(BaseSettings):
    project_path: Path = _project_path

    zone_info: ZoneInfo = ZoneInfo("Asia/Shanghai")

    debug: bool = True

    proxy_http_host: str | None = None

    proxy_http_port: int | None = None

    model_config = SettingsConfigDict(
        arbitrary_types_allowed=True, env_file=_dot_env_path, extra="allow"
    )

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
        self, api_info: ExchangeApiInfo | None = None, **kwargs
    ) -> sync_ccxt.Exchange:
        return self._create_exchange(sync_ccxt, api_info, **kwargs)

    def create_async_exchange(
        self, api_info: ExchangeApiInfo | None = None, **kwargs
    ) -> async_ccxt.Exchange:
        return self._create_exchange(async_ccxt, api_info, **kwargs)

    def create_async_exchange_public(
        self, exchange: str, **kwargs
    ) -> async_ccxt.Exchange:
        return self._create_exchange(
            async_ccxt, ExchangeApiInfo(exchange=exchange), **kwargs
        )

    def _create_exchange(
        self, module, api_info: ExchangeApiInfo | None = None, **kwargs
    ):
        api_info = api_info or self.api_info
        assert api_info.exchange in module.exchanges, (
            f"不支持的交易商: {api_info.exchange}"
        )
        kws = {"options": {"defaultType": "swap", "maxRetriesOnFailure": 3}}
        if api_info.exchange == "bitget":
            # 令bitget支持监听1s的频道
            kws["options"]["timeframes"] = {"1s": "1s"}
        if api_info.api_key:
            assert api_info.secret and api_info.password
            kws["apiKey"] = api_info.api_key
            kws["secret"] = api_info.secret
            kws["password"] = api_info.password

        proxy = self.get_proxy_http_base_url()
        if proxy:
            kws["https_proxy"] = proxy
            kws["ws_proxy"] = proxy

        kws.update(kwargs)
        exchange = getattr(module, api_info.exchange)(kws)
        return exchange

    def _config_logger(self):  # noqa
        from bitcoin.conf.logger import configure_logging

        configure_logging()
        script_file_name = Path(sys.modules["__main__"].__file__).stem
        start_time = datetime.datetime.now().strftime("%Y-%m-%d %H_%M_%S")
        log_file_path = str(
            self.project_path.joinpath(
                f"logs/{script_file_name}_{start_time}.log"
            )
        )
        logger.add(
            sink=log_file_path,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level="DEBUG",
            rotation="30 MB",  # 当文件大小达到 10MB 时，自动创建新的日志文件
            retention="90 days",  # 保留最近 7 天的日志文件
            compression="zip",  # 压缩旧的日志文件为 zip 格式
        )


load_dotenv(_dot_env_path)

settings = _Settings()
settings._config_logger()  # noqa


def __log_settings():
    try:
        settings.api_info  # noqa: for dev
    except ImportError:
        logger.warning("default _settings not found")
    proxy = settings.get_proxy_http_base_url()
    if proxy:
        logger.info(f"使用代理: {proxy}")
    else:
        logger.info("未使用代理")


__log_settings()
del __log_settings
