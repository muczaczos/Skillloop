"""Logging of the application's own modules (the "app" logger and its children).

Uvicorn configures only its own loggers; without this, INFO messages of the application are
dropped. Other libraries keep their defaults.

Everything logged by this code base must be free of tokens, passwords, headers and personal
data: fixed texts, codes, class names, request ids and code locations.
"""

import logging

APP_LOGGER_NAME = "app"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_HANDLER_NAME = "skillloop-app"


def configure_logging() -> None:
    """Send application messages of level INFO and above to stderr. Safe to call twice."""
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if any(handler.name == _HANDLER_NAME for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
