import os
import discord
from discord.ext import commands
from openai import AsyncOpenAI
from google import genai
import asyncio
import io
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# Ensure we can import the copied pdfgenerator
import sys
# The prompt is still in the root, so we go up twice to reach the root for the prompt path.
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, root_dir)

# Import pdfgenerator from the current directory
from pdfgenerator.generator import generate_pdf_report_bytes
from stockdb_client import fetch_db_data

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv() # Load root .env for discord token if needed

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "local").lower()
LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:1234/v1")
LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "deepseek-r1-distill-qwen-7b")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
REPORT_CACHE_DAYS = float(os.getenv("REPORT_CACHE_DAYS", "3"))

# Initialize Discord Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Initialize Clients based on provider
llm_client = None
gemini_client = None
anthropic_client = None

if LLM_PROVIDER == "gemini" and GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
elif LLM_PROVIDER == "anthropic" and ANTHROPIC_API_KEY:
    import anthropic
    anthropic_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
elif LLM_PROVIDER == "local":
    llm_client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key="not-needed")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print(f'Using {LLM_PROVIDER.upper()} for research generation.')

def _report_filename(ticker: str) -> str:
    return f"{ticker}_Research_Report.pdf"

async def find_recent_report(channel, ticker: str):
    """
    Look back through this channel's recent history for a PDF report the bot
    already posted for this ticker within REPORT_CACHE_DAYS. Sourcing this from
    Discord itself (rather than a local index) means it stays correct across
    restarts/redeploys with no extra persistence needed.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=REPORT_CACHE_DAYS)
    target_filename = _report_filename(ticker).upper()
    async for message in channel.history(after=cutoff, limit=1000, oldest_first=False):
        if message.author.id != bot.user.id:
            continue
        for attachment in message.attachments:
            if attachment.filename.upper() == target_filename:
                return message
    return None

@bot.command(name="research")
async def research(ctx, ticker: str):
    """Researches a stock ticker using an LLM and generates a PDF report."""
    ticker = ticker.upper()

    cached_message = await find_recent_report(ctx.channel, ticker)
    if cached_message:
        await cached_message.reply(
            f"📄 Here you go! **{ticker}** was already researched in the last "
            f"{REPORT_CACHE_DAYS:g} days — no need to regenerate."
        )
        return

    default_msg = "🔍 Fetching live data, cleaning, and computing for **{ticker}**. This might take a while..."
    custom_msg = os.getenv("DISCORD_LOADING_MESSAGE", default_msg)
    msg_text = custom_msg.replace("{ticker}", ticker)
    msg = await ctx.send(msg_text)

    try:
        # Load prompt template from the same directory as the script
        prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research_prompt_input.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt_template = f.read()
        
        # Fetch live data from the database
        db_data = fetch_db_data(ticker)

        # fetch_db_data() returns a plain sentinel string (rather than raising)
        # when stockDB has nothing for this symbol - fail loudly here instead of
        # silently asking the LLM to write a report about a ticker that doesn't exist.
        if db_data.startswith("No data found in stockDB for") or db_data.startswith("Error fetching from stockDB:"):
            await msg.delete()
            await ctx.reply(
                f"❌ **{ticker}** doesn't look like a valid ticker to me — I couldn't find any "
                f"market data for it. Double-check the symbol and try again."
            )
            return

        # Inject data into prompt
        prompt = prompt_template.replace("{TICKER}", ticker)
        prompt = prompt.replace("{FINVIZ_DATA}", db_data)

        # Call the selected LLM
        if LLM_PROVIDER == "gemini" and gemini_client:
            response = await asyncio.to_thread(gemini_client.models.generate_content, model='gemini-3.1-pro-preview', contents=prompt)
            llm_output = response.text
        elif LLM_PROVIDER == "anthropic" and anthropic_client:
            response = await anthropic_client.messages.create(
                model="claude-3-7-sonnet-20250219",
                max_tokens=4000,
                system="You are a professional equity research analyst.",
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            llm_output = response.content[0].text
        elif LLM_PROVIDER == "local" and llm_client:
            response = await llm_client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": "You are a professional equity research analyst."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=2500
            )
            llm_output = response.choices[0].message.content
        elif LLM_PROVIDER == "openrouter":
            from openai import AsyncOpenAI
            openrouter_client = AsyncOpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=os.getenv("OPENROUTER_API_KEY")
            )
            model = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
            response = await openrouter_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a professional equity research analyst."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=2500
            )
            llm_output = response.choices[0].message.content
        else:
            await ctx.send(f"❌ Error: Invalid LLM_PROVIDER '{LLM_PROVIDER}' configured or missing API key.")
            return

        # Generate PDF Bytes using generator
        await msg.edit(content=f"📄 Generating PDF report for **{ticker}**...")
        
        # The generator expects raw reports text mapped to one of its expected categories, 
        # or it can distill the raw text itself. Passing it as 'market_report' works.
        data = {"market_report": llm_output}
        
        # Run synchronous Playwright PDF generation in a thread
        pdf_bytes = await asyncio.to_thread(generate_pdf_report_bytes, data, ticker)
        
        # Save both MD and PDF to local output directory
        dt_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_dir = os.path.join(root_dir, "output", "reports", f"{ticker}_{dt_str}")
        os.makedirs(report_dir, exist_ok=True)
        
        md_path = os.path.join(report_dir, f"{ticker}_Research_Report.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(llm_output)
            
        pdf_path = os.path.join(report_dir, f"{ticker}_Research_Report.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
            
        print(f"Saved {ticker} reports to {report_dir}")
        
        # Send back to Discord
        pdf_file = discord.File(fp=io.BytesIO(pdf_bytes), filename=_report_filename(ticker))
        
        await msg.delete()
        await ctx.send(content=f"✅ Here is your research report for **{ticker}**:", file=pdf_file)

    except Exception as e:
        await msg.edit(content=f"❌ An error occurred while researching **{ticker}**: {str(e)}")
        print(f"Error researching {ticker}: {e}")

if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN environment variable not set.")
    else:
        print("Starting bot...")
        bot.run(TOKEN)
