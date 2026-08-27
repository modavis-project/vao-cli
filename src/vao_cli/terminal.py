from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class Terminal:
    color: bool = True
    quiet: bool = False

    def __post_init__(self) -> None:
        self.color = bool(
            self.color
            and sys.stdout.isatty()
            and not os.environ.get("NO_COLOR")
            and os.environ.get("TERM") != "dumb"
        )

    def style(self, value: str, code: str) -> str:
        return f"\033[{code}m{value}\033[0m" if self.color else value

    def heading(self, value: str) -> str:
        return self.style(value, "1;36")

    def success(self, value: str) -> str:
        return self.style(f"✓ {value}", "1;32")

    def warning(self, value: str) -> str:
        return self.style(f"⚠ {value}", "1;33")

    def failure(self, value: str) -> str:
        return self.style(f"✗ {value}", "1;31")

    def info(self, value: str) -> str:
        return self.style(f"● {value}", "36")

    def bar(self, value: int, maximum: int, *, width: int = 24) -> str:
        ratio = 0 if maximum <= 0 else min(1.0, max(0.0, value / maximum))
        filled = round(width * ratio)
        bar = "█" * filled + "░" * (width - filled)
        return f"{self.style(bar, '36')} {ratio * 100:5.1f}%"

    def transfer(self, label: str) -> Callable[[int, int], None] | None:
        if self.quiet or not sys.stderr.isatty():
            return None
        state = {"last": -1}

        def update(value: int, maximum: int) -> None:
            percent = 100 if maximum <= 0 else int(min(100, value * 100 / maximum))
            if percent == state["last"] and value < maximum:
                return
            state["last"] = percent
            suffix = "\n" if value >= maximum else ""
            sys.stderr.write(
                f"\r{self.info(label)}  {self.bar(value, maximum, width=20)}"
                f"  {value:,}/{maximum:,} bytes{suffix}"
            )
            sys.stderr.flush()

        return update

    @contextmanager
    def phase(self, label: str) -> Iterator[None]:
        if not self.quiet:
            print(self.info(label), file=sys.stderr)
        try:
            yield
        except Exception:
            if not self.quiet:
                print(self.failure(label), file=sys.stderr)
            raise
        else:
            if not self.quiet:
                print(self.success(label), file=sys.stderr)
