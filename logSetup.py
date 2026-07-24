import os
import sys
import logging

_names = {}


def register(conId: int, symbol: str) -> None:
    _names[conId] = symbol


def name(conId) -> str:
    return _names.get(conId, str(conId))


_LEVELS = {
    logging.DEBUG:    ("DEBUG", "\x1b[2m"),
    logging.INFO:     ("INFO ", ""),
    logging.WARNING:  ("WARN ", "\x1b[33m"),
    logging.ERROR:    ("ERROR", "\x1b[31m"),
    logging.CRITICAL: ("CRIT ", "\x1b[31;1m"),
}
_RESET = "\x1b[0m"


class _Formatter(logging.Formatter):

    def __init__(self, color: bool):
        super().__init__()
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        tag, tint = _LEVELS.get(record.levelno, ("?????", ""))
        ts  = self.formatTime(record, "%H:%M:%S") + f".{int(record.msecs):03d}"
        mod = record.name.split(".")[-1]
        if mod == "__main__":
            mod = "main"
        mod = mod[:16].ljust(16)
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        line = f"{ts} {tag} {mod} {msg}"
        if self._color and tint:
            return f"{tint}{line}{_RESET}"
        return line


def setup(level=logging.INFO) -> None:
    stream = sys.stderr
    color  = stream.isatty()
    if color and os.name == "nt":
        os.system("")

    handler = logging.StreamHandler(stream)
    handler.setFormatter(_Formatter(color))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    logging.getLogger("ib_async").setLevel(logging.WARNING)
    logging.getLogger("ib_async.client").setLevel(logging.WARNING)
    logging.getLogger("ib_async.wrapper").setLevel(logging.ERROR)
