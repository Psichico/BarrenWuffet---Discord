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
        snapshot = get_snapshot(ticker, ingest_if_missing=True, refresh_if_stale=True)
        if snapshot:
            # We want to give the LLM the summary text AND the raw fundamentals/company data
            # so it doesn't hallucinate missing data (like Q4 2026 revenue).
            summary = snapshot.get("summary_text", "")
            
            import json
            
            # Extract important data without flooding context window with 60 days of daily price bars
            extracted = {
                "company_overview": snapshot.get("company"),
                "fundamentals": snapshot.get("fundamentals"),
            }
            
            raw_data_str = json.dumps(extracted, indent=2)
            
            return f"{summary}\n\nRAW DATA (Use this for fundamental analysis):\n{raw_data_str}"
            
        return f"No data found in stockDB for {ticker}."
    except Exception as e:
        return f"Error fetching from stockDB: {e}"
