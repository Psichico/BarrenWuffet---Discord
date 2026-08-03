"""
Command-line interface.

    python -m stockdb.cli migrate                 # apply pending SQL migrations
    python -m stockdb.cli ingest MSFT [AAPL ...]  # fetch + store per ticker
    python -m stockdb.cli refresh                 # refresh all active tickers
    python -m stockdb.cli status [MSFT]           # coverage/freshness report

Flags for `ingest`/`refresh`:
    --force-full            ignore freshness; full pull of everything
    --no-write              dry run: fetch but do not write
    --only prices,news      restrict to these data types

This module also contains the small factory that builds a configured
AlphaVantageProvider (shared rate limiter) — the single place to swap in a
different default provider later.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from typing import List, Optional, Sequence

from . import agent_api
from .config import Settings, get_settings
from .db import connection, close_pool
from .errors import StockDBError
from .factory import build_provider
from .migrate import run_migrations
from .ingest.orchestrator import DATA_TYPES, ingest_ticker

log = logging.getLogger("stockdb")


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


# --- Commands --------------------------------------------------------------

def cmd_migrate(_args, _settings) -> int:
    applied = run_migrations()
    print(f"Applied {len(applied)} migration(s)." if applied else "Schema already up to date.")
    return 0


def cmd_ingest(args, settings) -> int:
    provider = build_provider(settings)
    only = _parse_only(args.only)
    exit_code = 0
    for symbol in args.symbols:
        try:
            report = ingest_ticker(
                symbol, provider, settings,
                only=only, force_full=args.force_full, no_write=args.no_write,
            )
        except StockDBError as exc:
            print(f"{symbol}: ERROR {exc}")
            exit_code = 1
            continue
        _print_report(report)
        if not report.ok:
            exit_code = 1
    return exit_code


def cmd_refresh(args, settings) -> int:
    """Refresh every ticker listed in watchlist.json."""
    import pathlib
    watchlist_path = pathlib.Path("watchlist.json")
    if not watchlist_path.exists():
        print(f"Error: Watchlist file not found at {watchlist_path.absolute()}")
        return 1

    try:
        with open(watchlist_path, "r", encoding="utf-8") as f:
            raw_symbols = json.load(f)
    except Exception as e:
        print(f"Error reading/parsing watchlist.json: {e}")
        return 1

    if not isinstance(raw_symbols, list):
        print("Error: watchlist.json must contain a JSON array of symbols.")
        return 1

    from .symbols import normalize_symbol
    from .ingest import upsert

    symbols = []
    for s in raw_symbols:
        if not isinstance(s, str):
            print(f"Warning: skipping non-string entry in watchlist: {s}")
            continue
        try:
            canon = normalize_symbol(s)
            symbols.append(canon)
        except Exception as e:
            print(f"Warning: skipping invalid symbol {s!r}: {e}")

    if not symbols:
        print("No valid symbols found in watchlist.json to refresh.")
        return 0

    # Ensure each symbol exists in the tickers table and is active
    with connection() as conn:
        with conn.transaction():
            for symbol in symbols:
                ticker_id = upsert.get_or_create_ticker(conn, symbol)
                conn.execute(
                    "UPDATE tickers SET active = TRUE WHERE id = %s",
                    (ticker_id,),
                )

    print(f"Refreshing {len(symbols)} ticker(s) from watchlist.json: {', '.join(symbols)}")
    args.symbols = symbols
    return cmd_ingest(args, settings)


def cmd_status(args, settings) -> int:
    symbol: Optional[str] = args.symbol
    with connection() as conn:
        if symbol:
            rows = conn.execute(
                """
                SELECT dc.data_type, dc.high_water_date, dc.last_fetched, dc.last_status
                  FROM data_coverage dc
                  JOIN tickers t ON t.id = dc.ticker_id
                 WHERE t.symbol = %s
                 ORDER BY dc.data_type
                """,
                (symbol.upper(),),
            ).fetchall()
            if not rows:
                print(f"No coverage recorded for {symbol.upper()}.")
                return 0
            print(f"Coverage for {symbol.upper()}:")
            print(f"  {'data_type':<14}{'high_water':<14}{'last_fetched':<28}status")
            for dt, hw, lf, st in rows:
                print(f"  {dt:<14}{str(hw or '-'):<14}{str(lf or '-'):<28}{st}")
        else:
            # DB-wide summary.
            tickers = conn.execute("SELECT count(*) FROM tickers").fetchone()[0]
            bars = conn.execute("SELECT count(*) FROM price_bars").fetchone()[0]
            news = conn.execute("SELECT count(*) FROM news").fetchone()[0]
            funds = conn.execute("SELECT count(*) FROM fundamentals").fetchone()[0]
            print("Database summary:")
            print(f"  tickers      : {tickers}")
            print(f"  price_bars   : {bars}")
            print(f"  news         : {news}")
            print(f"  fundamentals : {funds}")
    return 0


def cmd_snapshot(args, settings) -> int:
    """Build and print the point-in-time snapshot the data agent would receive."""
    as_of: Optional[date] = None
    if args.as_of:
        try:
            as_of = date.fromisoformat(args.as_of)
        except ValueError:
            print(f"--as-of must be YYYY-MM-DD, got {args.as_of!r}", file=sys.stderr)
            return 1
    snap = agent_api.get_snapshot(
        args.symbol, as_of,
        settings=settings,
        ingest_if_missing=not args.no_ingest,
    )
    if snap is None:
        print(f"No data for {args.symbol.upper()} (and ingest disabled or failed).")
        return 1
    if args.json:
        print(json.dumps(snap, indent=2, default=str))
    else:
        print(snap["summary_text"])
    return 0


# --- Helpers ---------------------------------------------------------------

def _parse_only(value: Optional[str]) -> Optional[List[str]]:
    """Parse --only 'prices,news' into a validated list of data types."""
    if not value:
        return None
    requested = [v.strip() for v in value.split(",") if v.strip()]
    invalid = [v for v in requested if v not in DATA_TYPES]
    if invalid:
        raise SystemExit(
            f"--only got unknown data type(s): {', '.join(invalid)}. "
            f"Valid: {', '.join(DATA_TYPES)}"
        )
    return requested


def _print_report(report) -> None:
    print(f"{report.symbol}:")
    for r in report.results:
        detail = f"rows={r.rows}" if r.status == "ok" else (r.error or "")
        print(f"  {r.data_type:<14}{r.decision:<7}{r.status:<7}{detail}")


# --- Argument parsing ------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stockdb", description="Stock data ingestion CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply pending SQL migrations")

    def add_ingest_flags(sp):
        sp.add_argument("--force-full", action="store_true", help="ignore freshness; full pull")
        sp.add_argument("--no-write", action="store_true", help="dry run; fetch but do not write")
        sp.add_argument("--only", help="comma-separated data types (e.g. prices,news)")

    p_ing = sub.add_parser("ingest", help="ingest one or more tickers")
    p_ing.add_argument("symbols", nargs="+", help="ticker symbol(s), e.g. MSFT AAPL")
    add_ingest_flags(p_ing)

    p_ref = sub.add_parser("refresh", help="refresh all active tickers")
    add_ingest_flags(p_ref)

    p_stat = sub.add_parser("status", help="coverage report")
    p_stat.add_argument("symbol", nargs="?", help="ticker (omit for DB-wide summary)")

    p_snap = sub.add_parser("snapshot", help="point-in-time consolidated snapshot")
    p_snap.add_argument("symbol", help="ticker symbol, e.g. MSFT")
    p_snap.add_argument("--as-of", help="point-in-time date YYYY-MM-DD (default: today)")
    p_snap.add_argument("--json", action="store_true", help="print full JSON instead of a summary")
    p_snap.add_argument("--no-ingest", action="store_true", help="do not fetch-on-miss")

    return p


_COMMANDS = {
    "migrate": cmd_migrate,
    "ingest": cmd_ingest,
    "refresh": cmd_refresh,
    "status": cmd_status,
    "snapshot": cmd_snapshot,
}


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    _setup_logging(settings.log_level)
    try:
        return _COMMANDS[args.command](args, settings)
    except StockDBError as exc:
        # Expected, typed failures print cleanly (no scary traceback).
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
