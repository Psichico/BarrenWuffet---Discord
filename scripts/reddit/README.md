# Reddit Answers Discord Bot

This bot listens to a configured Discord channel for stock tickers, searches Reddit Answers (an AI-powered search feature), extracts the summary text points along with their source citations, and sends a rich embed card back to the Discord channel.

## Prerequisites

1. **Python 3.10+**
2. **Google Chrome** (required for SeleniumBase UC mode)

## Installation

Install the required python packages:
```bash
pip install discord.py seleniumbase python-dotenv
```

For SeleniumBase to download/configure the correct ChromeDriver automatically:
```bash
seleniumbase install chromedriver
```

## Configuration

Copy or add the following variables to your `.env` file in the root directory:

```env
# Discord Bot Token
DISCORD_BOT_TOKEN=your_discord_bot_token_here

# Channel ID where the bot listens to tickers (e.g. AAPL, TSLA)
REDDIT_DISCORD_CHANNEL_ID=123456789012345678
```

## Running the Bot

Run the bot as a module from the root folder:
```bash
python -m scripts.reddit.reddit_bot
```
