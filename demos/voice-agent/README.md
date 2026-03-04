# Westbrook & Associates — AI Voice Agent

An AI-powered phone receptionist that qualifies law firm leads via voice calls. Built with Vapi.ai, Claude Sonnet, ElevenLabs, and Deepgram.

**Alex**, the AI receptionist, answers inbound calls 24/7, asks qualifying questions, scores leads on a 0–10 scale, and routes them accordingly — booking consultations for hot leads, sending nurture emails for warm leads, and redirecting cold leads to appropriate resources.

## How It Works

```
Caller dials phone number
        ↓
   Vapi.ai handles the call
   (ElevenLabs voice + Deepgram transcription + Claude Sonnet brain)
        ↓
   Alex asks 5 qualifying questions
        ↓
   Scores the lead (0–10)
        ↓
   ┌─────────────┬──────────────┬──────────────┐
   │ Score 7–10  │  Score 4–6   │  Score 1–3   │
   │  QUALIFIED  │   NURTURE    │   REDIRECT   │
   │             │              │              │
   │ Books a     │ Captures     │ Refers to    │
   │ consultation│ email, sends │ Legal Aid,   │
   │ appointment │ resources    │ State Bar,   │
   │             │              │ Small Claims │
   └─────────────┴──────────────┴──────────────┘
        ↓
   Lead logged to SQLite → visible on web dashboard
```

## The 5 Qualifying Questions

| # | Question | Max Points |
|---|----------|-----------|
| 1 | Type of legal matter | 2 pts |
| 2 | Timeline / urgency | 2 pts |
| 3 | Competition check (other attorneys consulted) | 2 pts |
| 4 | Intent signal (readiness to proceed) | 2 pts |
| 5 | Jurisdiction / zip code | 2 pts |

**Total: 10 points**

## Project Structure

```
demos/voice-agent/
├── server.py              # Flask webhook server + dashboard
├── vapi_setup.py          # Creates Vapi tools, assistant, phone number
├── prompts/
│   └── system_prompt.txt  # Alex's personality + scoring rubric
├── requirements.txt       # Python dependencies
├── .env.example           # Environment variable template
└── .gitignore
```

## Prerequisites

- **Python 3.9+**
- **ngrok** — to expose your local server to the internet
- **Vapi.ai account** — for the voice agent platform
- **Resend account** — for sending nurture emails (optional)

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

**Resend (for nurture emails):**
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
  Westbrook & Associates — Voice Agent Webhook Server
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

> **Note:** If this is your first time using ngrok, you'll need to sign up at https://dashboard.ngrok.com and run `ngrok config add-authtoken YOUR_TOKEN` first.

### 6. Run Vapi setup

In a separate terminal (with the venv activated):

```bash
source .venv/bin/activate
python vapi_setup.py setup
```

When prompted, paste your ngrok URL. The script will:
1. Create 4 tools (log_lead, check_availability, send_nurture_email, transfer_call)
2. Create the assistant ("Alex") with Claude Sonnet, ElevenLabs voice, and Deepgram transcription
3. Provision a phone number (830 area code)

You'll see output like:

```
Creating tools...
  Created tool 'log_lead': abc-123
  Created tool 'check_availability': def-456
  Created tool 'send_nurture_email': ghi-789
  Created tool 'transfer_call': jkl-012

Creating assistant...
  Created assistant: mno-345

Creating phone number...
  Phone number: +18301234567

============================================================
  Setup complete!
  Phone number: +18301234567
============================================================
```

### 7. Test it

Call the phone number from your cell phone. Alex will answer and walk you through the qualification process.

After the call, check the dashboard at **http://localhost:5002** to see the logged lead.

## Dashboard

The web dashboard at `http://localhost:5002` displays all leads in a table with:

- **Time** — when the call happened
- **Name** — caller's name
- **Case Type** — type of legal matter
- **Score** — qualification score (color-coded: green 7–10, yellow 4–6, red 1–3)
- **Routing** — QUALIFIED / NURTURE / REDIRECT badge
- **Phone** — caller's phone number
- **Email** — email if captured
- **Zip** — caller's zip code

The dashboard auto-refreshes every 10 seconds. Summary stats are shown in the header.

## API

### `GET /api/leads`

Returns all leads as JSON, ordered by most recent first.

```bash
curl http://localhost:5002/api/leads
```

```json
[
  {
    "id": 1,
    "caller_name": "Maria",
    "case_type": "personal injury",
    "case_summary": "Car accident last week...",
    "score": 9,
    "routing": "qualified",
    "email": "maria@example.com",
    "zip_code": "78852",
    "phone": "+18301234567",
    "call_id": "abc-123",
    "created_at": "2026-03-04T11:00:00"
  }
]
```

## Managing Vapi Resources

```bash
# View current config (assistant ID, phone number, tool IDs)
python vapi_setup.py status

# Delete all Vapi resources (tools, assistant, phone number)
python vapi_setup.py teardown
```

## Test Scenarios

### Qualified Lead (Score 7–10)
- Personal injury, car accident last week
- First call, friend referral
- Wants to act ASAP
- Zip: 78852 (Eagle Pass, TX)
- **Expected:** Consultation booked, green QUALIFIED badge

### Nurture Lead (Score 4–6)
- Considering divorce, separated 4 months
- Spoke with one other attorney
- Still figuring things out
- Zip: 78840 (Del Rio, TX)
- **Expected:** Email with resources sent, yellow NURTURE badge

### Redirect (Score 1–3)
- Landlord kept security deposit, 13 months ago
- Two attorneys already declined
- Not sure what to do
- Zip: 75201 (Dallas, TX)
- **Expected:** Referred to Legal Aid / small claims, red REDIRECT badge

## Tech Stack

| Component | Service |
|-----------|---------|
| Voice Agent Platform | [Vapi.ai](https://vapi.ai) |
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
Make sure both the Flask server and ngrok are running before making a call. The ngrok URL must match what you provided during `vapi_setup.py setup`. If you restart ngrok (which gives a new URL), you'll need to re-run setup or update the server URL in the Vapi dashboard.

### Nurture emails not sending
Verify your `RESEND_API_KEY` is set in `.env`. With the free Resend plan, you can only send to the email address you signed up with (use a verified domain for production).

### Call connects but Alex doesn't speak
Ensure your ElevenLabs API key is configured in the Vapi dashboard under Settings > Integrations.
