import os
import re
import json
import io
import shutil
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright

import markdown

def parse_markdown_to_html(md_text):
    """Converts markdown text to HTML using the python-markdown package."""
    if not md_text:
        return ""
    
    # Python-markdown requires a blank line before lists. LLMs often miss this.
    # We force a blank line before any bullet point that immediately follows text.
    import re
    md_text = re.sub(r'([^\n])\n(\s*[-*]\s)', r'\1\n\n\2', md_text)
    
    # Use the markdown package to render robust HTML
    html = markdown.markdown(md_text, extensions=['extra', 'sane_lists'])
    
    # We still want to preserve some stylistic choices, but markdown does the heavy lifting
    return html


def extract_section(text, start_header, next_header=None):
    """Extracts a section of text between two markdown headers."""
    pattern = rf"(?s){start_header}(.*?)" + (rf"(?={next_header})" if next_header else r"$")
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


_RATING_KEYWORDS = [
    (re.compile(r"\bstrong\s+buy\b|\baccumulate\b|\bbuy\b|\bbullish\b", re.IGNORECASE), "Buy"),
    (re.compile(r"\breduce\b|\btrim\b|\bsell\b|\bbearish\b", re.IGNORECASE), "Sell"),
    (re.compile(r"\bhold\b|\bneutral\b", re.IGNORECASE), "Hold"),
]


def _classify_rating_text(text):
    """Maps a short rating phrase (e.g. 'Strong Buy', 'Bearish') to Buy/Sell/Hold."""
    for pattern, label in _RATING_KEYWORDS:
        if pattern.search(text):
            return label
    return None


def extract_rating(summary_text, raw_text, ticker="UNKNOWN"):
    """Extracts the final Buy/Hold/Sell rating, preferring the Investment Summary
    section and falling back to the full report text. Returns (rating, rating_class).
    Defaults to Hold only when no rating signal can be found anywhere, and logs
    that miss so it's visible instead of silent."""
    label_pattern = (
        r"(?:^|\n)\s*(?:[-*•]\s*)?(?:\d\.\s*)?\*{0,2}"
        r"(?:Final\s+)?(?:Recommendation|Rating|Verdict)\*{0,2}\s*:?\s*\*{0,2}\s*(.*?)$"
    )

    for candidate_text in (summary_text, raw_text):
        if not candidate_text:
            continue
        match = re.search(label_pattern, candidate_text, re.MULTILINE | re.IGNORECASE)
        if match:
            rating_phrase = re.sub(r"[\*`|]+", " ", match.group(1)).strip()
            label = _classify_rating_text(rating_phrase)
            if label:
                return label, label.lower()

    # Last resort: scan the summary section itself for a standalone rating word,
    # taking the last match by position since the verdict tends to appear near the end.
    if summary_text:
        last_label, last_pos = None, -1
        for pattern, label in _RATING_KEYWORDS:
            for m in pattern.finditer(summary_text):
                if m.start() > last_pos:
                    last_pos, last_label = m.start(), label
        if last_label:
            return last_label, last_label.lower()

    print(f"WARNING: Could not parse a rating for {ticker} from LLM output; defaulting to Hold. "
          f"Summary snippet: {(summary_text or raw_text or '')[:200]!r}")
    return "Hold", "hold"

def parse_report_data(raw_text):
    """Extracts variables and sections from the LLM markdown output."""
    data = {
        "ticker_full": "",
        "ticker_only": "",
        "company_name": "",
        "timeframe": "12 months",
        "confidence": "Medium",
        "rating": "Hold",
        "rating_class": "hold",
        "one_line_thesis": "",
        "fundamentals_md": "",
        "thesis_validation_md": "",
        "sector_macro_md": "",
        "catalysts_md": "",
        "investment_summary_md": ""
    }
    
    # 1. Extract Header Info
    ticker_match = re.search(r"(?:\*\*|# )?Stock Ticker / Company Name:?(?:\*\*|) ([A-Z]+)\s*/\s*(.*?)$", raw_text, re.MULTILINE | re.IGNORECASE)
    if not ticker_match:
        # Fallback if there is no slash
        ticker_match = re.search(r"(?:\*\*|# )?Stock Ticker / Company Name:?(?:\*\*|) (.*?)$", raw_text, re.MULTILINE | re.IGNORECASE)
        if ticker_match:
            parts = ticker_match.group(1).split()
            data["ticker_only"] = parts[0]
            data["company_name"] = " ".join(parts[1:]) if len(parts) > 1 else parts[0]
            data["ticker_full"] = f"NASDAQ : {data['ticker_only']} · SECTOR"
    else:
        data["ticker_only"] = ticker_match.group(1).strip()
        data["company_name"] = ticker_match.group(2).strip()
        data["ticker_full"] = f"NASDAQ : {data['ticker_only']} · SECTOR"
    
    timeframe_match = re.search(r"(?:\d\.\s*)?(?:\*\*|)Expected (?:T|t)imeframe:?(?:\*\*|) (.*?)$", raw_text, re.MULTILINE | re.IGNORECASE)
    if not timeframe_match:
        timeframe_match = re.search(r"(?:\*\*|## )?Investment Thesis:?(?:\*\*|) (.*?)$", raw_text, re.MULTILINE | re.IGNORECASE)
    if timeframe_match:
        data["timeframe"] = timeframe_match.group(1).strip()
        
    confidence_match = re.search(r"(?:\d\.\s*)?(?:\*\*|)Confidence (?:L|l)evel:?(?:\*\*|) (.*?)$", raw_text, re.MULTILINE | re.IGNORECASE)
    if confidence_match:
        data["confidence"] = confidence_match.group(1).strip()

    # Attempt to grab a one line thesis (usually from thesis summary)
    thesis_lines = extract_section(raw_text, r"(?:\*\*|\#+\s*)?(?:1\.\s*)?(?:5-bullet )?investment thesis summary:?(?:\*\*|)", r"(?:\*\*|\#+\s*)?(?:\d\.\s*)?Final (?:R|r)ecommendation").split('\n')
    for line in thesis_lines:
        if line.strip().startswith('*') or line.strip().startswith('-'):
            data["one_line_thesis"] = re.sub(r'[\*\-]\s*', '', line.strip())
            data["one_line_thesis"] = re.sub(r'\*\*(.*?)\*\*', r'\1', data["one_line_thesis"])
            break

    # 2. Extract Sections (Lenient matching of headers)
    data["fundamentals_md"] = extract_section(raw_text, r"(?:\#+\s*)?(?:1\.\s*)?Fundamental Analysis", r"(?:\#+\s*)?(?:2\.\s*)?Thesis Validation")
    data["thesis_validation_md"] = extract_section(raw_text, r"(?:\#+\s*)?(?:2\.\s*)?Thesis Validation", r"(?:\#+\s*)?(?:3\.\s*)?Sector & Macro View")
    data["sector_macro_md"] = extract_section(raw_text, r"(?:\#+\s*)?(?:3\.\s*)?Sector & Macro View", r"(?:\#+\s*)?(?:4\.\s*)?Catalyst Watch")
    data["catalysts_md"] = extract_section(raw_text, r"(?:\#+\s*)?(?:4\.\s*)?Catalyst Watch", r"(?:\#+\s*)?(?:5\.\s*)?Investment Summary")
    
    # Exclude the fields we extracted for the cover page from the summary section HTML
    summary_raw = extract_section(raw_text, r"(?:\#+\s*)?(?:5\.\s*)?Investment Summary", r"(?:\#+\s*)?Conclusion")
    if not summary_raw:
        summary_raw = extract_section(raw_text, r"(?:\#+\s*)?(?:5\.\s*)?Investment Summary")

    data["rating"], data["rating_class"] = extract_rating(summary_raw, raw_text, ticker=data["ticker_only"] or "UNKNOWN")

    summary_clean = re.sub(r"(?:\d\.\s*)?(?:\*\*|)(?:Final Recommendation|Final Verdict):?(?:\*\*|).*", "", summary_raw, flags=re.MULTILINE|re.IGNORECASE)
    summary_clean = re.sub(r"(?:\d\.\s*)?(?:\*\*|)Confidence Level:?(?:\*\*|).*", "", summary_clean, flags=re.MULTILINE|re.IGNORECASE)
    summary_clean = re.sub(r"(?:\d\.\s*)?(?:\*\*|)Expected Timeframe:?(?:\*\*|).*", "", summary_clean, flags=re.MULTILINE|re.IGNORECASE)
    data["investment_summary_md"] = summary_clean.strip()

    return data


def build_html_report_text(final_state_or_data: dict, ticker: str, config=None, run_metadata: dict | None = None) -> str:
    """Reads template and populates it with parsed markdown data, returning populated HTML."""
    resources_root = Path(__file__).parent / "resources"
    template_path = resources_root / "investor-report-template.html"

    if not template_path.exists():
        raise FileNotFoundError(f"Required report template not found at {template_path}")

    html_template = template_path.read_text(encoding="utf-8")

    # Get the raw text from the bot payload
    raw_text = final_state_or_data.get("market_report", "")
    
    # Parse the markdown string into variables and sections
    data = parse_report_data(raw_text)
    
    # If the regex parsing failed to find a ticker, fallback to the provided argument
    if not data["ticker_only"]:
        data["ticker_only"] = ticker.upper()
        data["ticker_full"] = f"NASDAQ : {ticker.upper()} · SECTOR"
        data["company_name"] = ticker.upper()

    date_str = datetime.now().strftime("%d %B %Y").upper()

    # Apply substitutions
    subs = {
        "{{DATE}}": date_str,
        "{{TICKER_FULL}}": data["ticker_full"],
        "{{COMPANY_NAME}}": data["company_name"],
        "{{TICKER_ONLY}}": data["ticker_only"],
        "{{RATING_CLASS}}": data["rating_class"],
        "{{RATING}}": data["rating"].upper(),
        "{{TIMEFRAME}}": data["timeframe"],
        "{{CONFIDENCE}}": data["confidence"].upper(),
        "{{ONE_LINE_THESIS}}": data["one_line_thesis"],
        
        "{{FUNDAMENTALS}}": parse_markdown_to_html(data["fundamentals_md"]),
        "{{THESIS_VALIDATION}}": parse_markdown_to_html(data["thesis_validation_md"]),
        "{{SECTOR_MACRO}}": parse_markdown_to_html(data["sector_macro_md"]),
        "{{CATALYSTS}}": parse_markdown_to_html(data["catalysts_md"]),
        "{{INVESTMENT_SUMMARY}}": parse_markdown_to_html(data["investment_summary_md"]),
    }

    for k, v in subs.items():
        html_template = html_template.replace(k, str(v))

    return html_template


def generate_pdf_report(final_state_or_data: dict, ticker: str, output_path, config=None, run_metadata: dict | None = None):
    """Populates HTML and prints PDF via Playwright directly to output_path."""
    pdf_bytes = generate_pdf_report_bytes(final_state_or_data, ticker, config, run_metadata=run_metadata)
    Path(output_path).write_bytes(pdf_bytes)

def generate_pdf_report_bytes(final_state_or_data: dict, ticker: str, config=None, run_metadata: dict | None = None) -> bytes:
    """Populates HTML and prints PDF via Playwright, returning bytes."""
    resources_root = Path(__file__).parent / "resources"

    # Generate HTML content
    html_content = build_html_report_text(final_state_or_data, ticker, config, run_metadata=run_metadata)

    # Write temp html file next to fonts directory so fonts render correctly
    temp_html_path = resources_root / f"temp_report_{ticker}_{datetime.now().strftime('%Y%m%d%H%M%S')}.html"
    temp_html_path.write_text(html_content, encoding="utf-8")
    
    file_url = temp_html_path.resolve().as_uri()
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(file_url, wait_until="networkidle")
            pdf_bytes = page.pdf(
                width="8.5in",
                height="11in",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"}
            )
            browser.close()
            return pdf_bytes
    finally:
        if temp_html_path.exists():
            temp_html_path.unlink()
