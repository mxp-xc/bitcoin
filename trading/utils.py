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


def is_workday():
    """是否是工作日"""
    return datetime.datetime.now().isoweekday() <= 5
