import sys
import os
from datetime import date
from dotenv import load_dotenv

# Load local environment variables (if they exist)
load_dotenv()

from stockdb.agent_api import get_snapshot

def fetch_db_data(ticker: str) -> str:
    """
    Fetches data from the HerkshireBathaway stockdb.
    This replaces the need for yfinance or finviz.
    """
    try:
        # get_snapshot defaults to today's date
        # explicitly enabling ingest_if_missing and refresh_if_stale
        # Widened news window (90 days / up to 1000 items) so catalysts like the
        # last earnings call / guidance don't age out of a narrow default window -
        # build_text_summary() only ever prints the 5 newest headlines, so we also
        # pass a compact slice of the fuller news list through as raw data below.
        snapshot = get_snapshot(ticker, ingest_if_missing=True, refresh_if_stale=True, news_days=90, news_limit=1000)
        if snapshot:
            # We want to give the LLM the summary text AND the raw fundamentals/company data
            # so it doesn't hallucinate missing data (like Q4 2026 revenue).
            summary = snapshot.get("summary_text", "")

            import json

            # Compact news items (headline/date/sentiment only) so a 90-day window
            # doesn't blow up the prompt with full summaries/URLs for every item.
            recent_news = [
                {
                    "date": n.get("published_at"),
                    "headline": n.get("headline"),
                    "sentiment": n.get("sentiment_label"),
                }
                for n in (snapshot.get("news") or [])
            ]

            # Extract important data without flooding context window with 60 days of daily price bars
            extracted = {
                "company_overview": snapshot.get("company"),
                "fundamentals": snapshot.get("fundamentals"),
                "recent_news": recent_news,
            }

            raw_data_str = json.dumps(extracted, indent=2)

            return f"{summary}\n\nRAW DATA (Use this for fundamental analysis):\n{raw_data_str}"
            
        return f"No data found in stockDB for {ticker}."
    except Exception as e:
        return f"Error fetching from stockDB: {e}"
