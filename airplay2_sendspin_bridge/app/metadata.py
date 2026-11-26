"""Track info (title, artist, album, cover) from shairport-sync's metadata pipe.

shairport-sync writes items like

    <item><type>636f7265</type><code>6d696e6d</code><length>5</length>
    <data encoding="base64">
    SGVsbG8=</data></item>

into a named pipe. Type and code are 4-character codes in hex ("core"/"minm"
= title). A block of track metadata is framed by ssnc/mdst … ssnc/mden; the
cover comes separately as ssnc/PICT.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

log = logging.getLogger("metadata")

ITEM_RE = re.compile(
    rb"<item><type>([0-9a-fA-F]{8})</type><code>([0-9a-fA-F]{8})</code>"
    rb"<length>(\d+)</length>"
    rb'(?:\s*<data encoding="base64">\s*([A-Za-z0-9+/=\s]*?)\s*</data>)?'
    rb"\s*</item>"
)
MAX_BUFFER = 8 * 1024 * 1024  # a cover in base64 is far smaller

TEXT_FIELDS = {
    ("core", "minm"): "title",
    ("core", "asar"): "artist",
    ("core", "asal"): "album",
    ("core", "asaa"): "album_artist",
}


@dataclass(frozen=True)
class TrackInfo:
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    cover: bytes | None = None

    @property
    def empty(self) -> bool:
        return not (self.title or self.artist or self.album or self.cover)


def _code(hex8: bytes) -> str:
    return bytes.fromhex(hex8.decode()).decode("latin-1")


class MetadataParser:
    """Turns the byte stream into TrackInfo updates (pure logic, no I/O)."""

    def __init__(self) -> None:
        self.buffer = b""
        self.current = TrackInfo()
        self._pending: dict[str, str] | None = None

    def feed(self, data: bytes) -> list[TrackInfo | None]:
        """Returns the updates contained in data (None = playback ended)."""
        self.buffer += data
        updates: list[TrackInfo | None] = []
        end = 0
        for m in ITEM_RE.finditer(self.buffer):
            end = m.end()
            kind, code = _code(m.group(1)), _code(m.group(2))
            payload = b""
            if m.group(4):
                with contextlib.suppress(ValueError):
                    payload = base64.b64decode(b"".join(m.group(4).split()))
            update = self._item(kind, code, payload)
            if update is not False:
                updates.append(update)
        self.buffer = self.buffer[end:]
        if len(self.buffer) > MAX_BUFFER:  # garbage, start over
            self.buffer = b""
        return updates

    def _item(self, kind: str, code: str, payload: bytes):
        """One item. Returns a TrackInfo/None update, or False for no update."""
        if (kind, code) == ("ssnc", "mdst"):
            self._pending = {}
            return False
        field = TEXT_FIELDS.get((kind, code))
        if field:
            text = payload.decode("utf-8", errors="replace").strip()
            if self._pending is not None:
                self._pending[field] = text
                return False
            self.current = replace(self.current, **{field: text})
            return self.current
        if (kind, code) == ("ssnc", "mden"):
            if self._pending is None:
                return False
            # A new block replaces the text fields; the cover comes on its own.
            fields = {f: self._pending.get(f, "") for f in TEXT_FIELDS.values()}
            changed = any(getattr(self.current, f) != v for f, v in fields.items())
            self._pending = None
            if not changed:
                return False
            self.current = replace(self.current, cover=None, **fields)
            return self.current
        if (kind, code) == ("ssnc", "PICT"):
            self.current = replace(self.current, cover=payload or None)
            return self.current
        if (kind, code) == ("ssnc", "pend"):  # playback ended
            self.current = TrackInfo()
            self._pending = None
            return None
        return False


class MetadataReader(threading.Thread):
    """Reads the pipe in a thread and hands updates to the event loop."""

    def __init__(
        self,
        path: Path,
        loop: asyncio.AbstractEventLoop,
        callback: Callable[[TrackInfo | None], None],
    ) -> None:
        super().__init__(daemon=True, name=f"meta-{path.name}")
        self.path = path
        self.loop = loop
        self.callback = callback
        self.parser = MetadataParser()

    def ensure_fifo(self) -> None:
        if self.path.exists() and not self.path.is_fifo():
            self.path.unlink()
        if not self.path.exists():
            os.mkfifo(self.path, 0o666)

    def run(self) -> None:
        while True:
            try:
                self.ensure_fifo()
                # Blocks until shairport-sync opens the pipe for writing.
                with open(self.path, "rb", buffering=0) as pipe:
                    while chunk := pipe.read(65536):
                        for update in self.parser.feed(chunk):
                            self.loop.call_soon_threadsafe(self.callback, update)
            except OSError as err:
                log.warning("metadata pipe %s: %s", self.path, err)
                threading.Event().wait(2)
