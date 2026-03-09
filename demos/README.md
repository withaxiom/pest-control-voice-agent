# Demos (AXIOM Voice Agent)

This repo is a proof-of-capability demo. The runnable implementation lives in:
- `demos/voice-agent/`

## 60–90 second Loom script (what to say + show)

**0) Set the hook (5–10s)**
- "Most firms miss calls after hours. Those missed calls are lost consults. This voice agent answers 24/7, qualifies the lead, and routes them — without hiring more staff."

**1) Show the call (20–30s)**
- Call the number.
- Agent answers instantly.
- It asks the qualifying questions.

**2) Show the scoring + routing logic (10–15s)**
- Explain: score 0–10.
  - 7–10 = qualified (book consult)
  - 4–6 = nurture (capture email + send resources)
  - 1–3 = redirect (legal aid / bar referral / small claims)

**3) Show the dashboard (15–20s)**
- Open `http://localhost:5002`
- Show the lead row (case type, score, routing)

**4) Close (5–10s)**
- "If you want this for your firm, DM us and we’ll recommend the fastest automation win — bilingual (EN/ES) by default."

## Screenshot checklist (for the README / website)
- [ ] Dashboard with 3 leads (1 qualified, 1 nurture, 1 redirect)
- [ ] Close-up of a single lead row (score + routing badge)
- [ ] Vapi assistant config screen (redact keys)

## Quick demo scenarios (use these when testing)
### Qualified Lead (7–10)
- Personal injury, car accident last week
- Friend referral
- Wants to act ASAP
- Zip: 78852

### Nurture Lead (4–6)
- Divorce exploration, separated 4 months
- Talked to one attorney
- Still deciding
- Zip: 78840

### Redirect (1–3)
- Security deposit dispute, 13 months ago
- Multiple attorneys declined
- Zip: 75201
