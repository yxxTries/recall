"""Log Recall's CPU and RAM once a second to CSV, then print a summary.

CPU % is of the whole machine, as Task Manager shows it.
Usage: python scripts/perf_monitor.py [--minutes 5] [--pid PID] [--max-cpu 1.0]
"""
import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from recall.config import data_dir  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=5)
    parser.add_argument("--pid", type=int, help="defaults to the running Recall's PID file")
    parser.add_argument("--max-cpu", type=float, help="exit 1 if average CPU %% is above this")
    args = parser.parse_args()

    pid = args.pid or int((data_dir() / "recall.pid").read_text())
    proc = psutil.Process(pid)
    out = data_dir() / "perf" / f"perf-{datetime.now():%Y%m%d-%H%M%S}.csv"
    out.parent.mkdir(exist_ok=True)

    cpu_samples, rss_samples = [], []
    proc.cpu_percent(None)  # first call only primes the counter
    end = time.monotonic() + args.minutes * 60
    with out.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "cpu_percent", "rss_mb"])
        while time.monotonic() < end:
            time.sleep(1)
            cpu = proc.cpu_percent(None) / psutil.cpu_count()
            rss = proc.memory_info().rss / 2**20
            cpu_samples.append(cpu)
            rss_samples.append(rss)
            writer.writerow([datetime.now().isoformat(timespec="seconds"), f"{cpu:.2f}", f"{rss:.1f}"])

    avg_cpu = sum(cpu_samples) / len(cpu_samples)
    print(f"{len(cpu_samples)} samples -> {out}")
    print(f"CPU avg {avg_cpu:.2f}%  max {max(cpu_samples):.2f}%  |  RAM max {max(rss_samples):.0f} MB")
    if args.max_cpu is not None and avg_cpu > args.max_cpu:
        print(f"FAIL: average CPU above {args.max_cpu}%")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
