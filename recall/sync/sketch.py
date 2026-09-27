"""Fixed-size summaries of what the device has seen (no models).

SimHash spots lines that were already uploaded, even after small re-renders (a timestamp, a counter).
A count-min sketch counts how often each word appeared, so rare words weigh more than "app" or "text".
"""
import hashlib
import math
import re
from collections import OrderedDict
from pathlib import Path

import numpy as np

WORD = re.compile(r"[a-z0-9]+")


def hash64(text: str) -> int:
    return int.from_bytes(hashlib.blake2b(text.encode(), digest_size=8).digest(), "little")


def simhash(line: str) -> int:
    """64-bit fingerprint where similar lines differ in few bits: each word votes on every bit."""
    votes = [0] * 64
    for word in WORD.findall(line.lower()):
        h = hash64(word)
        for bit in range(64):
            votes[bit] += 1 if h >> bit & 1 else -1
    return sum(1 << bit for bit in range(64) if votes[bit] > 0)


class SeenLines:
    """Lines already sent, as SimHashes. Split into 8 bands of 8 bits: two hashes within 7 bits share a band.

    Short lines are noisy: a small edit flips about 7 bits, a different line of similar words 15 or more.
    """

    MAX_DISTANCE, BANDS = 7, 8

    def __init__(self, capacity: int = 20_000) -> None:
        self.capacity = capacity
        self._hashes: OrderedDict[int, None] = OrderedDict()  # oldest first
        self._bands: list[dict[int, set[int]]] = [{} for _ in range(self.BANDS)]

    def _keys(self, h: int) -> list[int]:
        return [h >> (8 * i) & 0xFF for i in range(self.BANDS)]

    def seen(self, line: str) -> bool:
        """True if a near-identical line was seen before; otherwise remembers this one."""
        h = simhash(line)
        keys = self._keys(h)
        for band, key in zip(self._bands, keys):
            if any(bin(h ^ other).count("1") <= self.MAX_DISTANCE for other in band.get(key, ())):
                return True
        self._hashes[h] = None
        for band, key in zip(self._bands, keys):
            band.setdefault(key, set()).add(h)
        if len(self._hashes) > self.capacity:
            old, _ = self._hashes.popitem(last=False)
            for band, key in zip(self._bands, self._keys(old)):
                band[key].discard(old)
        return False


class WordRarity:
    """How many snapshots each word appeared in, in a count-min sketch of fixed size (4 x 8192 x 4 bytes = 128 KB).

    Counts halve every day, so roughly the last week decides what's common.
    """

    DEPTH, WIDTH = 4, 8192

    def __init__(self) -> None:
        self.counts = np.zeros((self.DEPTH, self.WIDTH), dtype=np.float32)
        self.snapshots = 0.0

    def _slots(self, word: str) -> tuple[np.ndarray, np.ndarray]:
        h = hashlib.blake2b(word.encode(), digest_size=4 * self.DEPTH).digest()
        cols = np.frombuffer(h, dtype=np.uint32) % self.WIDTH
        return np.arange(self.DEPTH), cols

    def add(self, text: str) -> None:
        for word in set(WORD.findall(text.lower())):
            self.counts[self._slots(word)] += 1
        self.snapshots += 1

    def count(self, word: str) -> float:
        return float(self.counts[self._slots(word)].min())  # collisions only ever add, so the minimum is closest

    def idf(self, word: str) -> float:
        return math.log((self.snapshots + 1) / (self.count(word) + 1)) + 1

    def decay(self, factor: float = 0.5) -> None:
        self.counts *= factor
        self.snapshots *= factor

    def save(self, path: Path) -> None:
        np.savez(path, counts=self.counts, snapshots=self.snapshots)

    @classmethod
    def load(cls, path: Path) -> "WordRarity":
        rarity = cls()
        if path.exists():
            data = np.load(path)
            rarity.counts, rarity.snapshots = data["counts"], float(data["snapshots"])
        return rarity
