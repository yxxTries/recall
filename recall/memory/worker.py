"""Turns capture events into stored activity records on a below-normal-priority thread."""
import ctypes
import logging
import queue
import threading

from recall.memory.activity import ActivityTracker

log = logging.getLogger(__name__)
THREAD_PRIORITY_BELOW_NORMAL = -1
MAX_QUEUED = 1000
MAX_BATCH = 32  # events folded together, so a burst costs one write per activity


class MemoryWorker:
    def __init__(self, store) -> None:
        self.store = store
        self.tracker = ActivityTracker()
        self._queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUED)
        self._thread = threading.Thread(target=self._run, name="memory-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout=10)

    def submit(self, e: dict) -> None:
        """Never blocks a capture thread: when the queue is full the event is dropped."""
        try:
            self._queue.put_nowait(e)
        except queue.Full:
            log.warning("memory queue full, dropped a %s event", e["type"])

    def _run(self) -> None:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL)
        while True:
            batch = [self._queue.get()]  # blocks with no CPU while idle
            while len(batch) < MAX_BATCH and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            stop = None in batch
            events = [e for e in batch if e is not None]
            if events:
                try:
                    self._store(events)
                except Exception:
                    log.exception("storing memories failed")
            if stop:
                return

    def _store(self, events: list[dict]) -> None:
        records = {}
        for e in events:
            record = self.tracker.add(e)
            if record:
                records[record["activity_id"]] = record  # the latest state of each activity
        for record in records.values():
            self.store.upsert(record)
        if records:
            log.info("updated %d activities from %d events", len(records), len(events))
