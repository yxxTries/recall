"""Turns a capture event into memory chunks of about 300 tokens."""
import hashlib

MAX_CHARS = 1200  # ~300 tokens at ~4 characters per token


def _split_long(line: str, max_chars: int) -> list[str]:
    pieces = []
    while len(line) > max_chars:
        cut = line.rfind(" ", 0, max_chars)
        cut = cut if cut > 0 else max_chars
        pieces.append(line[:cut])
        line = line[cut:].lstrip()
    return pieces + ([line] if line else [])


def chunk_text(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Split on line boundaries; each chunk repeats the previous chunk's last line for context."""
    lines = [piece for line in text.splitlines() if line.strip() for piece in _split_long(line.strip(), max_chars)]
    chunks, current, size = [], [], 0
    for line in lines:
        if current and size + len(line) > max_chars:
            chunks.append("\n".join(current))
            current = [current[-1]]
            size = len(current[0]) + 1
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def chunk_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:32]


def chunk_event(e: dict) -> list[dict]:
    """Chunks for a "text" (or, later, "audio") capture event, carrying its metadata."""
    return [
        {
            "chunk_id": chunk_id(text),
            "text": text,
            "app": e.get("app", ""),
            "title": e.get("title", ""),
            "url": e.get("url", ""),
            "source": e.get("source", e["type"]),
            "time": e["time"],
        }
        for text in chunk_text(e["text"])
    ]
