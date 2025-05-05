# -*- coding: utf-8 -*-
import datetime

import aiohttp
from loguru import logger


def format_datetime(dt: datetime.datetime):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def get_undulate(price1: float, price2: float) -> float:
    """获取振幅"""
    return abs(price1 - price2) / price1


def get_undulate_percent(price1: float, price2: float) -> float:
    """获取振幅"""
    return get_undulate(price1, price2) * 100


def is_workday(dt: datetime.datetime | None = None):
    """是否是工作日
    工作日的范围是周一8点到周六的8点
    """
    # 策略修改, 统一按工作日来计算
    # return True
    dt = dt or datetime.datetime.now()
    weekday = dt.weekday()

    # 周一8点
    left = (dt - datetime.timedelta(days=weekday)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    # 周六8点
    right = (dt + datetime.timedelta(days=5 - weekday)).replace(
        hour=8, minute=0, second=0, microsecond=0
    )
    return left <= dt < right


async def send_wx_message(content, msg_type: str = "markdown", *, key: str):
    if not key:
        raise RuntimeError("send_wx_message key parameter must not be null")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send",
            params={
                "key": key,
            },
            json={
                "msgtype": msg_type,
                msg_type: {
                    "content": content,
                    "mentioned_list": [],
                },
            },
            ssl=False,
        ) as response:
            response.raise_for_status()
            result_json: dict = await response.json()
            if result_json.get("errcode") != 0:
                logger.error(
                    f"Failed to send wx message. result: {result_json}"
                )


async def log_and_send_wx_message(
    content,
    msg_type: str = "markdown",
    *,
    key: str | None = None,
    level: str = "info",
):
    getattr(logger, level)(content)
    if key:
        await send_wx_message(content, msg_type, key=key)
