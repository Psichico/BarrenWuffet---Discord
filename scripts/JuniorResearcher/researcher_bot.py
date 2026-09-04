import os
import discord
from discord.ext import commands
from openai import AsyncOpenAI
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
# LLM calls go through OpenRouter (OpenAI-compatible API).
OPENROUTER_API_KEY = os.getenv("OR_JuniorResearcherAPI")
# Blank ⇒ "openrouter/auto" (OpenRouter routes); pin a slug or "@preset/<name>"
# to choose the model on OpenRouter's side.
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/auto")
# low|medium|high|minimal for reasoning models; "" / unset ⇒ don't send it.
REASONING_EFFORT = os.getenv("OPENROUTER_REASONING_EFFORT", "").strip().lower()
REPORT_CACHE_DAYS = float(os.getenv("REPORT_CACHE_DAYS", "3"))

# Initialize Discord Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Single LLM client — OpenRouter. Fall back to a placeholder key so import never
# crashes; the `!research` handler checks OPENROUTER_API_KEY and errors cleanly.
ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY or "not-set",
)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print(f'Using OpenRouter ({OPENROUTER_MODEL}) for research generation.')

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

        # Call the LLM via OpenRouter
        if not OPENROUTER_API_KEY:
            await ctx.send("❌ Error: OR_JuniorResearcherAPI is not set.")
            return
        create_kwargs = dict(
            model=OPENROUTER_MODEL,
            messages=[
                {"role": "system", "content": "You are a professional equity research analyst."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            # Counts reasoning + visible tokens; give reasoning models room so the
            # report isn't cut off mid-section.
            max_tokens=int(os.getenv("OPENROUTER_MAX_TOKENS", "8000")),
        )
        if REASONING_EFFORT:
            create_kwargs["extra_body"] = {"reasoning": {"effort": REASONING_EFFORT}}
        response = await ai_client.chat.completions.create(**create_kwargs)
        choice = response.choices[0]
        llm_output = choice.message.content
        if getattr(choice, "finish_reason", "") == "length":
            await ctx.send(
                f"⚠️ **{ticker}**: the model hit its token limit and the report may be "
                f"truncated. Raise OPENROUTER_MAX_TOKENS or lower OPENROUTER_REASONING_EFFORT."
            )

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
