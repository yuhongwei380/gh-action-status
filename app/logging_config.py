from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from uvicorn.logging import AccessFormatter, DefaultFormatter

CHINA_TIME = timezone(timedelta(hours=8))


class ChinaTimeMixin(logging.Formatter):
    """Render container logs consistently in UTC+8, independent of host timezone."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        value = datetime.fromtimestamp(record.created, CHINA_TIME)
        return value.strftime(datefmt) if datefmt else value.isoformat(timespec="seconds")


class ChinaDefaultFormatter(ChinaTimeMixin, DefaultFormatter):
    pass


class ChinaAccessFormatter(ChinaTimeMixin, AccessFormatter):
    pass
