#!/usr/bin/env python3
"""
Self-scheduling runner for the Makro price-by-location updater.

Run this ONCE and it keeps running: it executes a full location-price update
immediately, then repeats on a fixed interval forever — no external cron needed.
The scraping logic stays in `update_makro_location_prices.py`; this file only adds
the scheduling loop, per-cycle log files, and graceful shutdown.

Run (foreground):
    cd backend
    python cron_update_makro_location_prices.py

Run (background, survives terminal close):
    cd backend
    nohup python cron_update_makro_location_prices.py > /tmp/makro_loc_runner.log 2>&1 &

Stop:  Ctrl+C (foreground) or `kill <pid>` (background) — it finishes the current
       HTTP request, then exits cleanly.

Env vars:
  MAKRO_LOC_INTERVAL_HOURS  hours between the START of each cycle (default 24).
                            If a cycle runs longer than this, the next starts immediately.
  MAKRO_LOC_RUN_AT_START    "1" (default) run once on startup; "0" wait one interval first.
  (plus the updater's own: DB_*, MAKRO_LOC_DELAY, MAKRO_LOC_TIMEOUT)

On Railway: run as a long-lived Worker/Service (NOT a Cron job — this schedules itself).
"""
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta

# Importing the updater runs its logging.basicConfig() (StreamHandler -> stdout).
import update_makro_location_prices as updater

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scraper-url", "logs")
logger = logging.getLogger("makro_loc_runner")

_stop = False


def _handle_signal(signum, _frame):
    """Ask the loop to stop after the current cycle."""
    global _stop
    _stop = True
    logger.info("Received signal %s — will stop after the current cycle.", signum)


def _swap_cycle_log_file(current_handler):
    """Detach the previous per-cycle file handler and attach a fresh timestamped one.
    Returns the new handler (or None if the log file could not be created)."""
    root = logging.getLogger()
    if current_handler is not None:
        root.removeHandler(current_handler)
        current_handler.close()
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, f"price_location_{datetime.now():%Y%m%d_%H%M%S}.log")
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        root.addHandler(handler)
        logger.info("Cycle log -> %s", path)
        return handler
    except OSError as e:
        logger.warning("Could not open cycle log file in %s: %s", LOG_DIR, e)
        return None


def _run_one_cycle() -> int:
    """Run a single full update, converting a crash into a non-zero code."""
    try:
        return updater.main()
    except Exception:
        logger.exception("FATAL: unhandled error during Makro location price update")
        return 2


def main() -> int:
    interval_hours = float(os.environ.get("MAKRO_LOC_INTERVAL_HOURS", 24))
    interval = max(60.0, interval_hours * 3600.0)  # floor 60s so a typo can't hot-loop
    run_at_start = os.environ.get("MAKRO_LOC_RUN_AT_START", "1") != "0"

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("=" * 70)
    logger.info("Makro location-price runner started (interval = %.2f h)", interval / 3600.0)
    logger.info("=" * 70)

    cycle_handler = None

    # Optionally wait one interval before the very first run.
    if not run_at_start and not _stop:
        logger.info("MAKRO_LOC_RUN_AT_START=0 — waiting %.2f h before first cycle.", interval / 3600.0)
        _sleep_until(time.monotonic() + interval)

    while not _stop:
        cycle_handler = _swap_cycle_log_file(cycle_handler)
        started = time.monotonic()
        rc = _run_one_cycle()
        elapsed = time.monotonic() - started
        logger.info("Cycle finished (rc=%d) in %.0fs.", rc, elapsed)

        if _stop:
            break

        next_at = started + interval
        sleep_for = max(0.0, next_at - time.monotonic())
        eta = (datetime.now() + timedelta(seconds=sleep_for)).strftime("%Y-%m-%d %H:%M:%S")
        if sleep_for == 0:
            logger.info("Cycle took longer than the interval — starting next immediately.")
        else:
            logger.info("Next cycle at ~%s (sleeping %.0fs).", eta, sleep_for)
        _sleep_until(next_at)

    logger.info("Runner stopped.")
    return 0


def _sleep_until(deadline_monotonic: float):
    """Sleep in short slices so a stop signal is honored within ~1s."""
    while not _stop and time.monotonic() < deadline_monotonic:
        time.sleep(min(1.0, deadline_monotonic - time.monotonic()))


if __name__ == "__main__":
    sys.exit(main())
