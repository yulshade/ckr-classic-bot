import builtins
import sys
from datetime import datetime

from bot import main

# Some Windows console code pages (e.g. cp874, cp1252) can't encode the emoji
# used in print() throughout this app, which crashes it before it can start.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_original_print = builtins.print


def _print_with_datetime(*args, **kwargs):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _original_print(f"[{timestamp}]", *args, **kwargs)


builtins.print = _print_with_datetime

if __name__ == "__main__":
    main()
