import os
import sys
import json
import logging
import requests
from dotenv import load_dotenv

try:
    from fastapi import FastAPI, HTTPException, BackgroundTasks
    from pydantic import BaseModel
    from fpdf import FPDF
except ImportError as e:
    print(f"Error: Missing dependency: {e}")
    print("Please run: pip install fastapi uvicorn fpdf2 pydantic requests python-dotenv")
    sys.exit(1)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("server_service")

DISCORD_NEWS_WEBHOOK_URL = os.getenv("DISCORD_NEWS_WEBHOOK_URL")

app = FastAPI(title="Stock Analysis Queue Server")

# In-memory queue (in production, use SQLite/PostgreSQL/Redis)
PENDING_TICKERS = []


class AnalysisRequest(BaseModel):
    ticker: str


class AnalysisSubmission(BaseModel):
    ticker: str
    report_text: str


class PDFReport(FPDF):
    """Simple PDF styling helper using FPDF2."""
    def header(self):
        self.set_font("helvetica", "B", 15)
        self.cell(0, 10, "Stock Analysis Report", border=False, ln=True, align="C")
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


def send_to_discord(ticker: str, report_text: str):
    """Compiles the report to PDF and sends it via Discord Webhook."""
    if not DISCORD_NEWS_WEBHOOK_URL:
        logger.error("DISCORD_NEWS_WEBHOOK_URL is not configured.")
        return

    pdf_filename = f"{ticker}_Analysis_Report.pdf"
    
    # Generate PDF
    try:
        pdf = PDFReport()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        
        pdf.set_font("helvetica", "B", 12)
        pdf.cell(0, 10, f"Ticker: {ticker}", ln=True)
        pdf.set_font("helvetica", "I", 10)
        pdf.cell(0, 8, "Generated via Local LLM (Studio LM + Llama)", ln=True)
        pdf.ln(5)
        
        pdf.set_font("helvetica", size=10)
        cleaned_content = report_text.encode("latin-1", "replace").decode("latin-1")
        pdf.multi_cell(0, 6, txt=cleaned_content)
        pdf.output(pdf_filename)
        
        # Post to Discord Webhook
        payload = {"content": f"📊 **New Analysis Report Available!** Here is the deep research for **{ticker}**:"}
        with open(pdf_filename, "rb") as f:
            files = {
                "payload_json": (None, json.dumps(payload), "application/json"),
                "file": (pdf_filename, f, "application/pdf")
            }
            logger.info(f"Sending PDF for {ticker} to Discord webhook...")
            resp = requests.post(DISCORD_NEWS_WEBHOOK_URL, files=files)
            if resp.status_code >= 300:
                logger.error(f"Discord API returned status code {resp.status_code}: {resp.text}")
            else:
                logger.info("Successfully posted report to Discord.")
                
    except Exception as e:
        logger.exception("Error generating or sending PDF report")
    finally:
        # Clean up local PDF file
        if os.path.exists(pdf_filename):
            os.remove(pdf_filename)


@app.post("/request-analysis")
def request_analysis(req: AnalysisRequest):
    """Endpoint called by user's script to queue a ticker."""
    ticker = req.ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker cannot be empty")
    
    PENDING_TICKERS.append(ticker)
    logger.info(f"Queued analysis request for: {ticker}")
    return {"status": "queued", "ticker": ticker, "position": len(PENDING_TICKERS)}


@app.get("/pending-analysis")
def get_pending():
    """Endpoint polled by the home laptop to get work."""
    if PENDING_TICKERS:
        ticker = PENDING_TICKERS.pop(0)
        logger.info(f"Dispatched ticker {ticker} to client")
        return {"ticker": ticker}
    return {"ticker": None}


@app.post("/submit-report")
def submit_report(submission: AnalysisSubmission, background_tasks: BackgroundTasks):
    """Endpoint called by the home laptop to upload the report."""
    logger.info(f"Received completed report for {submission.ticker}")
    # Run PDF generation & discord post asynchronously in background
    background_tasks.add_task(send_to_discord, submission.ticker, submission.report_text)
    return {"status": "accepted"}


if __name__ == "__main__":
    import uvicorn
    # In Render, port is supplied via the PORT environment variable
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
