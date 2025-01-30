# -*- coding: utf-8 -*-
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Result(Generic[T], BaseModel):
    success: bool
    data: T | None
    message: str

    @classmethod
    def of(cls, data: T):
        return cls(success=True, data=data, message="success")

    @classmethod
    def failed(cls, message: str):
        return cls(success=False, data=None, message=message)
