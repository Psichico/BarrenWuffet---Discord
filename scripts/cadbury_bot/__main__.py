"""Manual trigger:  python -m scripts.cadbury_bot [--force] [--date YYYY-MM-DD]

Builds and emails the recap right now, regardless of the time of day. Useful for
a first live smoke test or to re-send a day that failed. Without ``--force`` it
still respects the once-per-day marker.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from scripts.cadbury_bot import build_cadbury


def main(argv=None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    parser = argparse.ArgumentParser(prog="python -m scripts.cadbury_bot")
    parser.add_argument("--force", action="store_true",
                        help="send even if a recap already went out for the day")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="recap this calendar day's items (default: today ET)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--db", default=os.environ.get("DEDUPE_PATH", "./seen.db"))
    args = parser.parse_args(argv)

    bot = build_cadbury(args.config, args.db)
    if bot is None:
        print("cadbury_bot is disabled (no config block or enabled: false).")
        return 1

    sent = bot.run_now(force=args.force, day=args.date)
    print("Recap sent." if sent else "No email sent (see logs).")
    return 0 if sent else 2


if __name__ == "__main__":
    raise SystemExit(main())
