# -*- coding: utf-8 -*-
import datetime


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
    dt = dt or datetime.datetime.now()
    weekday = dt.weekday()

    # 周一8点
    left = (dt - datetime.timedelta(days=weekday)).replace(hour=8, minute=0, second=0, microsecond=0)
    # 周六8点
    right = (dt + datetime.timedelta(days=5 - weekday)).replace(hour=8, minute=0, second=0, microsecond=0)
    return left <= dt < right
