# Green Shield Pest Control — AI Receptionist

An AI-powered phone receptionist that qualifies pest control leads via voice calls. **Built by AXIOM Collective.**

**Maria**, the bilingual AI receptionist, answers inbound calls 24/7 in English and Spanish. She identifies the pest problem, scores urgency, qualifies the lead on a 0–10 scale, and routes them — booking free inspections for hot leads, sending quotes for warm leads, and providing prevention tips for cool leads.

## Bilingual (EN/ES)

Maria detects the caller's language automatically and switches seamlessly between English and Spanish — no prompting needed. She uses natural South Texas / Mexican Spanish register, making callers feel at home.

## How It Works

```
Customer calls phone number
        ↓
   Vapi.ai handles the call
   (ElevenLabs voice + Deepgram transcription + Claude Sonnet brain)
        ↓
   Maria asks 5 qualifying questions
   (bilingual — detects EN/ES automatically)
        ↓
   Scores the lead (0–10)
        ↓
   ┌─────────────┬──────────────┬──────────────┐
   │ Score 7–10  │  Score 4–6   │  Score 1–3   │
   │  HOT LEAD   │  WARM LEAD   │  COOL LEAD   │
   │             │              │              │
   │ Books free  │ Captures     │ Provides     │
   │ inspection  │ info, sends  │ pest         │
   │ (same-day   │ quote via    │ prevention   │
   │ for urgency)│ email        │ tips         │
   └─────────────┴──────────────┴──────────────┘
        ↓
   Lead logged to SQLite → visible on web dashboard
```

## The 5 Qualifying Questions

| # | Question | Max Points |
|---|----------|-----------|
| 1 | Type of pest (termites/rodents = high, general bugs = medium) | 2 pts |
| 2 | Urgency — active infestation/safety concern vs. preventive | 2 pts |
| 3 | Property type + size (commercial = high, large residential = standard) | 2 pts |
| 4 | Ready to book? (today/this week = high, just quoting = low) | 2 pts |
| 5 | Service history (new customer vs. returning) | 2 pts |

**Total: 10 points**

## Project Structure

```
demos/voice-agent/
├── server.py              # Flask webhook server + dashboard
├── vapi_setup.py          # Creates Vapi tools, assistant, phone number
├── prompts/
│   └── system_prompt.txt  # Maria's personality + scoring rubric
├── requirements.txt       # Python dependencies
├── .env.example           # Environment variable template
└── .gitignore
```

## Prerequisites

- **Python 3.9+**
- **ngrok** — to expose your local server to the internet
- **Vapi.ai account** — for the voice agent platform
- **Resend account** — for sending quote emails (optional)

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/withaxiom/voice-agent.git
cd voice-agent/demos/voice-agent

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Get your API keys

**Vapi.ai:**
1. Sign up at https://dashboard.vapi.ai
2. Go to **Dashboard > API Keys**
3. Copy your API key
4. Add your **ElevenLabs API key** in Dashboard > Settings > Integrations (required for the voice)

**Resend (for quote emails):**
1. Sign up at https://resend.com
2. Go to **Dashboard > API Keys**
3. Copy your API key

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your keys:

```
VAPI_API_KEY=your_vapi_api_key_here
RESEND_API_KEY=your_resend_api_key_here
```

### 4. Start the webhook server

```bash
source .venv/bin/activate
python server.py
```

The server starts on **http://localhost:5002**. You should see:

```
============================================================
  Green Shield Pest Control — AI Receptionist Webhook Server
  Dashboard: http://localhost:5002
  Webhook:   http://localhost:5002/webhook/tools
============================================================
```

### 5. Start ngrok

In a separate terminal:

```bash
ngrok http 5002
```

Copy the public URL (e.g. `https://abc123.ngrok-free.dev`). This is your webhook URL.

> **Note:** If this is your first time using ngrok, sign up at https://dashboard.ngrok.com and run `ngrok config add-authtoken YOUR_TOKEN` first.

### 6. Run Vapi setup

In a separate terminal (with the venv activated):

```bash
source .venv/bin/activate
python vapi_setup.py setup
```

When prompted, paste your ngrok URL. The script will:
1. Create 4 tools (log_lead, check_availability, send_nurture_email, transfer_call)
2. Create the assistant ("Maria") with Claude Sonnet, ElevenLabs voice, and Deepgram transcription
3. Provision a phone number (830 area code — Texas)

### 7. Test it

Call the phone number. Maria will answer and walk through the qualification.

After the call, check the dashboard at **http://localhost:5002** to see the logged lead.

## Dashboard

The web dashboard at `http://localhost:5002` displays all leads with:

- **Time** — when the call happened
- **Name** — caller's name
- **Pest Type** — type of pest problem
- **Score** — qualification score (color-coded: green 7–10, yellow 4–6, red 1–3)
- **Routing** — HOT / WARM / COOL badge
- **Status** — new / contacted / inspection_booked / scheduled / closed
- **Phone** — caller's phone number
- **Email** — email if captured
- **Zip** — caller's zip code

Auto-refreshes every 10 seconds. Summary stats shown in the header.

## API

### `GET /api/leads`

Returns all leads as JSON, ordered by most recent first.

```bash
curl http://localhost:5002/api/leads
```

## Test Scenarios

### Hot Lead (Score 7–10)
- Scorpion problem, found three in the living room this week
- Has small children — urgent safety concern
- 2,500 sq ft home in San Antonio, TX
- Wants someone out today or tomorrow
- **Expected:** Same-day inspection booked, green HOT badge

### Warm Lead (Score 4–6)
- Seeing cockroaches occasionally in kitchen
- Medium urgency, wants to compare prices first
- Standard 3-bedroom home
- **Expected:** Quote email sent, yellow WARM badge

### Cool Lead (Score 1–3)
- Saw one ant outside near the porch
- Not really a problem yet, just curious
- Just moved in, wants general info
- **Expected:** Prevention tips provided, red COOL badge

## Demo Talk Track (60–90 seconds)

> "This is Maria — Green Shield Pest Control's AI receptionist. She answers every call, in English *and* Spanish, 24/7. Watch what happens when a customer calls about a scorpion problem..."
>
> [Make the call — Maria answers in English, switches to Spanish when the customer prefers]
>
> "She's qualifying the lead in real time — identifying the pest, assessing urgency, checking if they're ready to book. She scores this a 9 out of 10 — urgent, safety concern, ready to go — and books a same-day inspection."
>
> "Meanwhile, the dashboard here shows the new lead coming in live. Pest type, score, routing — everything the team needs to follow up."
>
> "No hold music. No missed calls. Every lead captured, qualified, and ready for your technicians."

## Tech Stack

| Component | Service |
|-----------|---------|
| Voice Agent Platform | Vapi.ai |
| AI Model | Claude Sonnet (Anthropic) |
| Voice Synthesis | ElevenLabs |
| Speech-to-Text | Deepgram Nova 2 |
| Webhook Server | Flask (Python) |
| Email | Resend |
| Database | SQLite |
| Tunnel | ngrok |

## Troubleshooting

### Server shows 500 errors on tool calls
Check the server logs for the full payload. Vapi sends tool call parameters under the `parameters` key (not `arguments`). The server handles both formats.

### ngrok requires authentication
Run `ngrok config add-authtoken YOUR_TOKEN` with your token from https://dashboard.ngrok.com/get-started/your-authtoken.

### No leads appearing on dashboard
Make sure both the Flask server and ngrok are running before making a call. If you restart ngrok (new URL), re-run `vapi_setup.py setup` or update the webhook URL in the Vapi dashboard.

### Quote emails not sending
Verify your `RESEND_API_KEY` is set in `.env`. Free Resend plan can only send to your signup email — use a verified domain for production.

### Call connects but Maria doesn't speak
Ensure your ElevenLabs API key is configured in Vapi dashboard under Settings > Integrations.

---

*Built by AXIOM Collective — Day 2 Showcase Demo*
