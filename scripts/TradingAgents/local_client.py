import os
import sys
import time
import logging
from dotenv import load_dotenv

try:
    import requests
    import openai
except ImportError as e:
    print(f"Error: Missing dependency: {e}")
    print("Please run: pip install requests openai python-dotenv")
    sys.exit(1)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("local_client")

# Configuration
SERVER_URL = os.getenv("SERVER_URL", "http://your-render-server.onrender.com")
LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "llama-model-name")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", 10))

ai_client = openai.OpenAI(base_url=LM_STUDIO_URL, api_key="lm-studio")


def poll_and_process():
    """Polls the server queue and processes any tickers found."""
    logger.info(f"Starting local polling daemon (polling {SERVER_URL} every {POLL_INTERVAL_SECONDS}s)...")
    
    while True:
        try:
            # 1. Poll the server for work
            poll_endpoint = f"{SERVER_URL.rstrip('/')}/pending-analysis"
            resp = requests.get(poll_endpoint, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                ticker = data.get("ticker")
                
                if ticker:
                    logger.info(f"🔔 Found pending analysis request for: {ticker}")
                    
                    # 2. Query Local LLM
                    system_prompt = (
                        "You are a professional financial analyst. Provide a comprehensive stock report. "
                        "Include sections for Company Overview, Key Metrics, Financial Health, "
                        "Recent News/Catalysts, Technical Analysis summary, and Investment Thesis (Bull & Bear case)."
                    )
                    user_prompt = f"Please perform a deep analysis on the stock ticker: {ticker}."
                    
                    logger.info(f"Running LLM analysis on local laptop using {LLM_MODEL_NAME}...")
                    
                    response = ai_client.chat.completions.create(
                        model=LLM_MODEL_NAME,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        temperature=0.7,
                        max_tokens=2048
                    )
                    report_text = response.choices[0].message.content
                    logger.info("LLM analysis generated successfully.")
                    
                    # 3. Submit report back to the server
                    submit_endpoint = f"{SERVER_URL.rstrip('/')}/submit-report"
                    submit_data = {
                        "ticker": ticker,
                        "report_text": report_text
                    }
                    
                    logger.info(f"Uploading analysis back to server {submit_endpoint}...")
                    submit_resp = requests.post(submit_endpoint, json=submit_data, timeout=15)
                    
                    if submit_resp.status_code == 200:
                        logger.info(f"✓ Analysis for {ticker} successfully uploaded and queued for Discord delivery.")
                    else:
                        logger.error(f"Failed to upload analysis: {submit_resp.status_code} | {submit_resp.text}")
            else:
                logger.error(f"Failed to poll server: {resp.status_code}")
                
        except requests.RequestException as e:
            logger.error(f"Network error: {e}")
        except Exception as e:
            logger.exception("Unexpected error in main polling loop")
            
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    if SERVER_URL == "http://your-render-server.onrender.com" or not SERVER_URL:
        print("Please configure your 'SERVER_URL' in a .env file first.")
        sys.exit(1)
        
    poll_process = poll_and_process()
