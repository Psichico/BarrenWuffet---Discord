import os
import sys
import logging
from dotenv import load_dotenv

# Try to import discord and other required libraries, explaining how to install them if missing
try:
    import discord
    from discord.ext import commands
    import openai
    from fpdf import FPDF
except ImportError as e:
    print(f"Error: Missing dependency: {e}")
    print("Please run: pip install discord.py openai fpdf2 python-dotenv")
    sys.exit(1)

# Load environment variables (such as DISCORD_BOT_TOKEN)
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("local_bot")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "llama-model-name") # LM studio default accepts any string if only one model is loaded

if not DISCORD_BOT_TOKEN:
    logger.error("DISCORD_BOT_TOKEN is not set in environment variables.")
    print("Please add 'DISCORD_BOT_TOKEN=your_token_here' to a .env file or your environment.")
    sys.exit(1)

# Initialize clients
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
ai_client = openai.OpenAI(base_url=LM_STUDIO_URL, api_key="lm-studio")


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


def generate_pdf_report(ticker: str, content: str, output_path: str):
    """Compiles the text report into a clean PDF."""
    pdf = PDFReport()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # Title & Metadata
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 10, f"Ticker: {ticker}", ln=True)
    pdf.set_font("helvetica", "I", 10)
    pdf.cell(0, 8, "Generated via Local LLM (Studio LM + Llama)", ln=True)
    pdf.ln(5)
    
    # Body text
    pdf.set_font("helvetica", size=10)
    # Clean non-latin characters for simple PDF rendering
    cleaned_content = content.encode("latin-1", "replace").decode("latin-1")
    pdf.multi_cell(0, 6, txt=cleaned_content)
    
    pdf.output(output_path)


@bot.event
async def on_ready():
    logger.info(f"Bot connected as {bot.user}")
    print(f"\nSuccessfully logged in as: {bot.user}")
    print("Bot is listening for commands like: !analyze AAPL\n")


@bot.command(name="analyze")
async def analyze(ctx, ticker: str):
    ticker = ticker.upper()
    await ctx.send(f"🔍 Starting deep stock research on **{ticker}** using local LLM. This may take a moment...")
    
    try:
        # Prompt definition
        system_prompt = (
            "You are a professional financial analyst. Provide a comprehensive stock report. "
            "Include sections for Company Overview, Key Metrics, Financial Health, "
            "Recent News/Catalysts, Technical Analysis summary, and Investment Thesis (Bull & Bear case)."
        )
        user_prompt = f"Please perform a deep analysis on the stock ticker: {ticker}."

        logger.info(f"Querying local LLM at {LM_STUDIO_URL} for ticker {ticker}...")
        
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
        logger.info(f"Received LLM analysis. Compiling PDF...")
        
        # Output paths
        pdf_path = f"{ticker}_Analysis_Report.pdf"
        generate_pdf_report(ticker, report_text, pdf_path)
        
        logger.info(f"Uploading PDF {pdf_path} to channel...")
        with open(pdf_path, "rb") as f:
            await ctx.send(
                content=f"📈 **Research Complete!** Here is your PDF stock report for **{ticker}**:",
                file=discord.File(f, filename=pdf_path)
            )
            
        # Clean up local file
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
            
    except Exception as e:
        logger.exception("Failed to run analysis or generate report")
        await ctx.send(f"❌ An error occurred during analysis for **{ticker}**: `{str(e)}`")


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
