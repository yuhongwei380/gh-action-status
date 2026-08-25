import json
import logging
from pathlib import Path

from app.logging_config import ChinaDefaultFormatter


def test_container_logging_config_includes_timestamps():
    config_path = Path(__file__).parents[1] / "app" / "logging.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert "%(asctime)s" in config["formatters"]["default"]["format"]
    assert "%z" in config["formatters"]["default"]["datefmt"]
    assert "%(asctime)s" in config["formatters"]["access"]["format"]
    assert config["formatters"]["default"]["()"] == "app.logging_config.ChinaDefaultFormatter"
    assert config["formatters"]["access"]["()"] == "app.logging_config.ChinaAccessFormatter"


def test_log_timestamp_is_rendered_in_utc_plus_eight():
    record = logging.LogRecord("app.alerts", logging.INFO, __file__, 1, "message", (), None)
    record.created = 0
    formatter = ChinaDefaultFormatter(
        fmt="%(asctime)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z", use_colors=False
    )
    assert formatter.format(record) == "1970-01-01T08:00:00+0800 message"
