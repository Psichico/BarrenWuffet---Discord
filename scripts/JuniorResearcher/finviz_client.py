import yfinance as yf
import asyncio

async def fetch_finviz_data(ticker: str) -> str:
    """
    Fetches live financial data for the given ticker using yfinance.
    Returns a formatted string of key metrics to inject into the LLM prompt.
    """
    try:
        def get_data():
            stock = yf.Ticker(ticker)
            info = stock.info
            
            metrics = []
            keys_to_extract = {
                "marketCap": "Market Cap",
                "trailingPE": "Trailing P/E Ratio",
                "forwardPE": "Forward P/E",
                "trailingEps": "EPS (ttm)",
                "fiftyTwoWeekLow": "52W Low",
                "fiftyTwoWeekHigh": "52W High",
                "revenueGrowth": "Revenue Growth",
                "profitMargins": "Profit Margins"
            }
            
            for key, label in keys_to_extract.items():
                val = info.get(key, "N/A")
                metrics.append(f"- {label}: {val}")
                
            return "\n".join(metrics)
            
        # yfinance makes blocking network calls, so we run it in a thread
        data = await asyncio.to_thread(get_data)
        return data
        
    except Exception as e:
        return f"(ERROR fetching financial data: {str(e)})"
