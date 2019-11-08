"""Module defining functions for printing to the Console."""

from datetime import datetime as dt
from logging import DEBUG, Formatter, getLogger, StreamHandler
from typing import Callable, Dict, List, Tuple, Union


NOCOLOR = lambda s: s


try:
    from blessings import Terminal
except ImportError:

    class Terminal:
        def __getattr__(self, attr):
            return NOCOLOR


T = Terminal()

fmt = Formatter(
    "<%(asctime)s.%(msecs)d> :: %(name)s // %(levelname)s: %(message)s", "%H:%M:%S"
)
fmt.msec_format = "%s.%02d"
ch = StreamHandler()
ch.setFormatter(fmt)


def newLogger(name: str = ""):
    logger = getLogger(name.upper())
    logger.addHandler(ch)
    logger.setLevel(DEBUG)
    return logger


colors: Dict[str, Tuple[Callable[[str], str], str, int]] = {
    "": (T.white, "", 1),
    "con": (T.white, " ++", 1),
    "dcon": (T.bright_black, "X- ", 1),
    "win": (T.bright_green, "\o/", 1),
    "diff": (T.white, "*- ", 2),
    "err": (T.magenta, "x!x", 1),
    "recv": (T.white, "-->", 3),
    "send": (T.bright_black, "<--", 3),
    "tab": (T.white, "   ", 3),
    "warn": (T.magenta, "(!)", 3),
    "info": (T.cyan, "(!)", 2),
}


hl_method = T.bold_yellow
hl_remote = T.bold_magenta


class _Printer:
    def __init__(self, verbosity: int = 2):
        self.file = None
        self.output_line = print
        self.startup: dt = dt.utcnow()
        self.verbosity: int = verbosity

    def emit(self, etype: str, text: str, color=None):
        now = dt.utcnow()
        p_color, prefix, pri, *tc = colors.get(etype) or (T.white, etype, 4)
        if tc:
            tc = tc[0]

        if self.file:
            print(
                "<{}> {} {}".format(now.isoformat(sep=" ")[:-3], prefix, text),
                file=self.file,
                flush=True,
            )
        if pri <= self.verbosity:
            self.output_line(
                f"<{str(now)[11:-4]}> {p_color(prefix)} {(color or tc or NOCOLOR)(text)}"
            )


P = _Printer()


def echo(etype: str, text: Union[str, List[str]] = None, color=""):
    if text is None:
        etype, text = "info", etype

    if type(text) == list:
        for line in text:
            P.emit(etype, line, color)
    else:
        P.emit(etype, text, color)


def err(text: str, exc: BaseException = None):
    if exc is not None:
        text += f" {type(exc).__name__} - {exc}"
    echo("err", text, T.red)


def warn(text: str):
    echo("warn", text, T.bright_yellow)


def set_verbosity(n: int):
    P.verbosity = n
