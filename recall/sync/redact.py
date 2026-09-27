"""Redaction before upload: secrets and PII become placeholders like ⟨SECRET:3⟩ (no models).

Known key formats, `password = ...` assignments, emails, phone numbers and high-entropy tokens are
replaced. The same value always gets the same placeholder, so the cloud can still tell that two
episodes mention the same address; the vault that maps placeholders back stays on this device.
"""
import math
import re
import sqlite3
import threading
from collections import Counter
from pathlib import Path

KEY_FORMATS = [
    r"sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}",  # Anthropic, OpenAI
    r"gh[pousr]_[A-Za-z0-9]{36,}", r"github_pat_[A-Za-z0-9_]{40,}",
    r"(?:AKIA|ASIA)[0-9A-Z]{16}",  # AWS access key IDs
    r"xox[abposr]-[A-Za-z0-9-]{10,}",  # Slack
    r"gsk_[A-Za-z0-9]{40,}",  # Groq
    r"sb_(?:secret|publishable)_[A-Za-z0-9_-]{20,}",  # Supabase
    r"AIza[0-9A-Za-z_-]{35}",  # Google
    r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",  # JWTs
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
]
RULES = [  # (kind, pattern); a named group "value" marks the part to hide, otherwise the whole match
    ("SECRET", re.compile("|".join(KEY_FORMATS))),
    ("SECRET", re.compile(r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\b[\"']?\s*[:=]\s*"
                          r"[\"']?(?P<value>[^\s\"',;]{6,})")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")),
    ("PHONE", re.compile(r"(?<![\w.:/-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)\s?|\d{2,4}[\s.-])\d{3,4}[\s.-]\d{3,4}"
                         r"(?![\w.:/-])")),
]
TOKEN = re.compile(r"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_=-]{24,}(?![A-Za-z0-9+/_=-])")
PLACEHOLDER = re.compile(r"⟨(SECRET|EMAIL|PHONE):(\d+)⟩")
MIN_ENTROPY = 4.0  # bits per character; English identifiers sit around 3.5, random base64 near 5


def entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def looks_random(token: str) -> bool:
    """A long token mixing cases and digits with high entropy, unlike words, paths and snake_case names."""
    classes = sum(any(test(c) for c in token) for test in (str.islower, str.isupper, str.isdigit))
    words = re.findall(r"[a-z]{4,}", token)
    return classes == 3 and entropy(token) >= MIN_ENTROPY and sum(map(len, words)) < len(token) / 2


class Vault:
    """Placeholder numbers and the values behind them, kept only on this device."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("create table if not exists vault (n integer primary key, kind text not null, "
                        "value text not null unique)")

    def placeholder(self, kind: str, value: str) -> str:
        with self._lock, self.db:
            self.db.execute("insert or ignore into vault (kind, value) values (?, ?)", (kind, value))
            n = self.db.execute("select n from vault where value = ?", (value,)).fetchone()[0]
        return f"⟨{kind}:{n}⟩"

    def restore(self, text: str) -> str:
        with self._lock:
            values = dict(self.db.execute("select n, value from vault"))
        return PLACEHOLDER.sub(lambda m: values.get(int(m[2]), m[0]), text)


def redact(text: str, vault: Vault) -> str:
    for kind, pattern in RULES:
        def hide(m: re.Match, kind=kind) -> str:
            if "value" not in m.re.groupindex:
                return vault.placeholder(kind, m[0])
            start, end = m.span("value")
            return m[0][:start - m.start()] + vault.placeholder(kind, m["value"]) + m[0][end - m.start():]
        text = pattern.sub(hide, text)
    return TOKEN.sub(lambda m: vault.placeholder("SECRET", m[0]) if looks_random(m[0]) else m[0], text)
