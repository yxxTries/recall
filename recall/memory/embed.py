"""On-device text embeddings: bge-small-en-v1.5 through ONNX (fastembed), no PyTorch."""
import os
import threading

import numpy as np

from recall.config import models_dir

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # Windows without dev mode: copies, not links

MODEL = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384
# bge retrieves better when short queries carry this instruction; passages go in as-is.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class Embedder:
    def __init__(self, threads: int = 2) -> None:
        self.threads = threads  # caps ONNX's CPU use during bursts
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self):
        with self._lock:  # loaded on first use (~0.5 s), then shared
            if self._model is None:
                from fastembed import TextEmbedding

                self._model = TextEmbedding(MODEL, cache_dir=str(models_dir()), threads=self.threads)
            return self._model

    def passages(self, texts: list[str]) -> np.ndarray:
        return np.array(list(self._get_model().embed(texts)), dtype=np.float32)

    def query(self, text: str) -> np.ndarray:
        return self.passages([QUERY_INSTRUCTION + text])[0]
