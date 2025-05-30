# -*- coding: utf-8 -*-
import datetime
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
    YamlConfigSettingsSource,
)

from .schema import ExchangeConfiguration

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


_allow_envs = ["production", "local"]


def get_config_files(filename: str, dir_path: Path, envs: list[str] | None = None) -> list[Path]:
    envs = envs or _allow_envs
    result = [dir_path / filename]
    name, suffix = filename.rsplit(".")
    for env in envs:
        result.append(dir_path.joinpath(f"{name}-{env}.{suffix}"))
    return result


class _Settings(BaseSettings):
    project_path: Path = Path(__file__).parent.parent.parent.resolve()

    zone_info: ZoneInfo = ZoneInfo("Asia/Shanghai")

    exchange: ExchangeConfiguration

    debug: bool = True

    model_config = SettingsConfigDict(
        arbitrary_types_allowed=True,
        env_file=project_path / ".env",
        toml_file=get_config_files("application.toml", project_path),
        yaml_file=get_config_files("application.yaml", project_path),
        extra="allow",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return super().settings_customise_sources(
            settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
        ) + (TomlConfigSettingsSource(settings_cls), YamlConfigSettingsSource(settings_cls))

    def model_post_init(self, context: Any, /) -> None:
        self._config_logger()
        proxy = self.exchange.get_proxy_http_base_url()
        if proxy:
            logger.info(f"使用代理: {proxy}")
        else:
            logger.info("未使用代理")

    def _config_logger(self):  # noqa
        from bitcoin.conf.logger import configure_logging

        configure_logging()
        script_file_name = Path(sys.modules["__main__"].__file__).stem
        start_time = datetime.datetime.now().strftime("%Y-%m-%d %H_%M_%S")
        log_file_path = str(self.project_path.joinpath(f"logs/{script_file_name}_{start_time}.log"))
        logger.add(
            sink=log_file_path,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level="DEBUG",
            rotation="30 MB",  # 当文件大小达到 10MB 时，自动创建新的日志文件
            retention="7 days",  # 保留最近 7 天的日志文件
            compression="zip",  # 压缩旧的日志文件为 zip 格式
        )


settings = _Settings()  # noqa
