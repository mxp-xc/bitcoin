import datetime

from tortoise import Model, fields


class HistoryKLine(Model):
    symbol: str = fields.CharField(max_length=32, null=False)
    opening_time: datetime.datetime = fields.DatetimeField(null=False)
    opening_price: float = fields.FloatField(null=False)
    closing_price: float = fields.FloatField(null=False)
    highest_price: float = fields.FloatField(null=False)
    lowest_price: float = fields.FloatField(null=False)
    volume: float = fields.FloatField(null=False)

    class Meta:
        table = "history_klines"

        unique_together = (("symbol", "opening_time"),)
