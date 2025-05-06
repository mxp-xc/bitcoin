import inspect
import logging
import logging.config

from loguru import logger


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding Loguru level if it exists.
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message.
        frame, depth = inspect.currentframe(), 0
        while frame:
            filename = frame.f_code.co_filename
            is_logging = filename == logging.__file__
            is_frozen = "importlib" in filename and "_bootstrap" in filename
            if depth > 0 and not (is_logging or is_frozen):
                break
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def configure_logging():
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "loggers": {
                "tortoise.db_client": {"level": "INFO"},
                "tortoise": {"level": "INFO"},
                "aiosqlite": {"level": "INFO"},
                "asyncio": {"level": "INFO"},
                "ccxt": {"level": "INFO"},
            },
        }
    )
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
