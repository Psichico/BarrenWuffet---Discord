# Stock Analysis Bot — PDF Report Pipeline

This module provides two ways to add a stock analysis bot that uses a local LLM (running via **LM Studio + Llama**) at your home, compiles reports into **PDFs**, and posts them to your **Discord** server.

---

## Prerequisites & Installation

On your **local laptop**, install the required Python packages:
```bash
pip install discord.py openai fpdf2 python-dotenv requests
```

### 1. LM Studio Setup
1. Launch **LM Studio** on your local laptop.
2. Download and load a Llama model (e.g. `Llama-3-8B-Instruct`).
3. Navigate to the **Local Server** tab (developer icon on the left sidebar).
4. Click **Start Server** (typically runs on `http://localhost:1234`).

### 2. Discord Developer Portal Setup
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application**, give it a name (e.g. `Llama Analyst`), and go to the **Bot** tab.
3. Click **Reset Token** and copy the bot token.
4. In the **Privileged Gateway Intents** section, enable **Message Content Intent**.
5. Go to the **OAuth2 -> URL Generator** tab, select the `bot` scope, check `Send Messages` and `Attach Files` permissions, copy the generated URL, paste it into a browser, and add the bot to your server.

---

## Option A: Server-Mediated Flow (Web Server + Polling)

Use this option if you want to keep the bot logic hosted on the server, but only offload LLM processing to your local laptop.

```
┌──────────────────┐               ┌──────────────┐
│  Python Script   │ ──(Request)──▶│ Render Server│
│ (Trigger Ticker) │               │   (FastAPI)  │
└──────────────────┘               └──────────────┘
                                          │
                                       (Polls)
                                          ▼
                                   ┌──────────────┐
                                   │ Local Laptop │ ◀──(Llama)──▶ LM Studio
                                   └──────────────┘
```

### 1. Deployment (Server-side)
1. Add the code from [server_service.py](file:///J:/Antigravity/Richie%20Rich/BarrenWuffet%20-%20Discord/scripts/TradingAgents/server_service.py) to a new web service on Render (change `type` in `render.yaml` to `web`).
2. Expose the port Render provides (via `$PORT` env variable) and start it using:
   ```bash
   uvicorn scripts.TradingAgents.server_service:app --host 0.0.0.0 --port 8000
   ```
3. Set `DISCORD_NEWS_WEBHOOK_URL` in Render's environment variables.

### 2. Running (Laptop-side)
1. In your local folder, create a `.env` file containing:
   ```env
   SERVER_URL=https://your-render-app.onrender.com
   LM_STUDIO_URL=http://localhost:1234/v1
   LLM_MODEL_NAME=meta-llama-3-8b-instruct  # Specify the model loaded in LM Studio
   POLL_INTERVAL_SECONDS=10
   ```
2. Start the local daemon:
   ```bash
   python scripts/TradingAgents/local_client.py
   ```

### 3. Requesting Analysis
To trigger research, send a POST request to your Render server:
```bash
curl -X POST https://your-render-app.onrender.com/request-analysis \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL"}'
```

---

## Option B: Direct Local-Gateway Bot (Recommended & Easiest)

This runs the Discord Bot connection directly from your home laptop. It uses an outbound connection, meaning **you don't need any public port configuration, firewalls, or paid Render tiers.**

```
┌─────────────┐                    ┌──────────────┐
│   Discord   │ ──(!analyze AAPL)─▶│ Local Laptop │ ◀──(Llama)──▶ LM Studio
│   Channel   │ ◀──(Uploads PDF)── │  (discord.py)│
└─────────────┘                    └──────────────┘
```

### Running Option B
1. Create a `.env` file on your local laptop containing:
   ```env
   DISCORD_BOT_TOKEN=your-discord-bot-token
   LM_STUDIO_URL=http://localhost:1234/v1
   LLM_MODEL_NAME=meta-llama-3-8b-instruct
   ```
2. Run the bot script on your laptop:
   ```bash
   python scripts/TradingAgents/local_bot.py
   ```
3. In any Discord channel where the bot has access, type:
   ```text
   !analyze MSFT
   ```
4. The bot will query LM Studio, write the markdown report to a styled PDF, and upload it directly back to the channel.
