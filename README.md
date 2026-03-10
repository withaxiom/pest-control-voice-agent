# AXIOM Voice Agent (Law Firm Lead Qualification)

A production-style demo voice receptionist that answers calls 24/7, qualifies leads, scores them (0–10), and routes them:
- **Qualified (7–10):** books a consultation
- **Nurture (4–6):** captures email + sends resources
- **Redirect (1–3):** sends the caller to appropriate alternatives (Legal Aid, Bar referrals, small claims, etc.)

Built on **Vapi.ai** (voice platform) with **Claude Sonnet** (reasoning), **Deepgram** (transcription), and **ElevenLabs** (voice).

## Best for
- Immigration / personal injury / family law firms
- Clinics and professional services that miss calls after hours
- Bilingual firms (EN/ES) — easy to extend

## What to show in a 60–90s demo
1. Call the number → receptionist answers instantly
2. Ask 5 qualifying questions
3. Lead score + routing outcome
4. Open the dashboard → lead is logged + visible immediately

## Demo implementation
The full runnable demo lives here:
- `demos/voice-agent/`

Start with:
- `demos/voice-agent/README.md`

## Repo status
This repo is intentionally minimal: it’s a **proof-of-capability** demo and a base for client builds.

Built by **AXIOM Collective** (EN/ES AI automation).

## How to Sell This (AXIOM Notes)

**Who it’s for:** firms that miss calls after-hours (or during busy hours) and lose consults.

**Core promise:** “Answer every call, qualify every lead, route the right ones — 24/7.”

**Proof path:**
- Demo talk track: `demos/README.md`
- Runnable demo: `demos/voice-agent/`

**Next obvious upgrade:** bilingual EN/ES qualification flow.
