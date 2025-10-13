# -*- coding: utf-8 -*-
import logging
from logging import StreamHandler, Formatter

def setup_logger(name: str = "agent", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        ch = StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(ch)
    return logger
