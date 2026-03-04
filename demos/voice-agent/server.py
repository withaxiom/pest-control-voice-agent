"""
Westbrook & Associates — Voice Agent Webhook Server
Handles Vapi tool calls, stores leads, serves dashboard.
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta

import resend
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template_string, g

load_dotenv()

app = Flask(__name__)
resend.api_key = os.environ.get("RESEND_API_KEY", "")

DB_PATH = "leads.db"


# ─── Database ──────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_name TEXT NOT NULL,
            case_type TEXT,
            case_summary TEXT,
            score INTEGER NOT NULL,
            routing TEXT NOT NULL,
            email TEXT,
            zip_code TEXT,
            phone TEXT,
            call_id TEXT,
            created_at TEXT NOT NULL
        )
    """)
    db.commit()
    db.close()


# ─── Tool Handlers ─────────────────────────────────────────────────────────

def handle_log_lead(args, call_id=None):
    db = get_db()
    db.execute(
        """INSERT INTO leads (caller_name, case_type, case_summary, score, routing, email, zip_code, call_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            args.get("caller_name", "Unknown"),
            args.get("case_type"),
            args.get("case_summary"),
            args.get("score", 0),
            args.get("routing", "unknown"),
            args.get("email"),
            args.get("zip_code"),
            call_id,
            datetime.now().isoformat(),
        ),
    )
    db.commit()
    return f"Lead {args.get('caller_name')} logged successfully. Score: {args.get('score')}/10, Routing: {args.get('routing')}."


def handle_check_availability(args):
    today = datetime.now()
    slots = []
    day = today + timedelta(days=1)
    count = 0
    while count < 3:
        if day.weekday() < 5:  # Mon-Fri
            slots.append({
                "date": day.strftime("%A, %B %d"),
                "times": ["10:00 AM", "2:00 PM"],
                "attorney": "Attorney Reynolds" if count == 0 else "Attorney Kim",
            })
            count += 1
        day += timedelta(days=1)

    return json.dumps({
        "available_slots": slots,
        "note": "Consultations are 30 minutes. Free for first-time clients.",
    })


def handle_send_nurture_email(args):
    name = args.get("name", "there")
    email = args.get("email")
    case_type = args.get("case_type", "your legal matter")

    if not email:
        return "Error: no email address provided."

    html = f"""
    <div style="font-family: Georgia, serif; max-width: 600px; margin: 0 auto; color: #2D1B2E;">
        <div style="background: #1a1a2e; padding: 24px; text-align: center;">
            <h1 style="color: #D4AF37; margin: 0; font-size: 24px;">Westbrook & Associates</h1>
            <p style="color: rgba(255,255,255,0.6); margin: 4px 0 0;">Attorneys at Law</p>
        </div>

        <div style="padding: 32px 24px;">
            <p>Dear {name},</p>

            <p>Thank you for reaching out to us regarding {case_type}. We understand this can be
            a stressful time, and we appreciate your trust in contacting our firm.</p>

            <p>As promised, here are some resources that may be helpful:</p>

            <ul style="line-height: 1.8;">
                <li><strong>Know Your Rights</strong> — A guide to understanding your legal options</li>
                <li><strong>What to Expect</strong> — How the legal process works, step by step</li>
                <li><strong>Document Checklist</strong> — What to gather before your consultation</li>
            </ul>

            <p>When you're ready, we'd love to schedule a free 30-minute consultation to discuss
            your situation in detail.</p>

            <div style="text-align: center; margin: 32px 0;">
                <a href="https://westbrookassociates.com/book"
                   style="background: #D4AF37; color: #1a1a2e; padding: 14px 32px;
                          text-decoration: none; font-weight: bold; border-radius: 4px;">
                    Book a Free Consultation
                </a>
            </div>

            <p>Warm regards,<br>
            <strong>The Westbrook & Associates Team</strong><br>
            <span style="color: #888;">Eagle Pass, Texas</span></p>
        </div>

        <div style="background: #f5f5f5; padding: 16px; text-align: center; font-size: 12px; color: #888;">
            Westbrook & Associates | Eagle Pass, TX<br>
            This email is not legal advice.
        </div>
    </div>
    """

    try:
        resend.Emails.send({
            "from": "Westbrook & Associates <onboarding@resend.dev>",
            "to": [email],
            "subject": f"Resources from Westbrook & Associates — {case_type.title()}",
            "html": html,
        })
        return f"Nurture email sent to {email} successfully."
    except Exception as e:
        return f"Email failed: {str(e)}"


def handle_transfer_call(args):
    return json.dumps({
        "destination": {
            "type": "number",
            "number": "+18305555555",
            "message": "Transferring you to our reception desk now.",
        }
    })


TOOL_HANDLERS = {
    "log_lead": handle_log_lead,
    "check_availability": handle_check_availability,
    "send_nurture_email": handle_send_nurture_email,
    "transfer_call": handle_transfer_call,
}


# ─── Webhook Routes ────────────────────────────────────────────────────────

@app.route("/webhook/tools", methods=["POST"])
def webhook_tools():
    data = request.get_json()
    message = data.get("message", {})
    call_id = message.get("call", {}).get("id")

    results = []
    for tool_call in message.get("toolCallList", []):
        handler = TOOL_HANDLERS.get(tool_call["name"])
        if handler:
            if tool_call["name"] == "log_lead":
                result = handler(tool_call.get("arguments", {}), call_id)
            else:
                result = handler(tool_call.get("arguments", {}))
        else:
            result = f"Unknown tool: {tool_call['name']}"

        results.append({
            "toolCallId": tool_call["id"],
            "name": tool_call["name"],
            "result": result,
        })

    return jsonify({"results": results})


@app.route("/webhook/vapi", methods=["POST"])
def webhook_vapi():
    data = request.get_json()
    message = data.get("message", {})
    event_type = message.get("type")

    if event_type == "end-of-call-report":
        print(f"\n{'='*50}")
        print(f"Call ended: {message.get('endedReason')}")
        transcript = message.get("artifact", {}).get("transcript", "")
        if transcript:
            print(f"Transcript preview: {transcript[:200]}...")
        print(f"{'='*50}\n")

    elif event_type == "status-update":
        status = message.get("status")
        print(f"Call status: {status}")

    return jsonify({"ok": True})


# ─── Dashboard ─────────────────────────────────────────────────────────────

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Westbrook & Associates — Lead Dashboard</title>
<style>
  :root {
    --dark: #1a1a2e;
    --gold: #D4AF37;
    --green: #27ae60;
    --yellow: #f39c12;
    --red: #e74c3c;
    --bg: #f8f9fa;
    --text: #2D1B2E;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }

  .header {
    background: var(--dark);
    padding: 20px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header h1 { color: var(--gold); font-size: 20px; }
  .header p { color: rgba(255,255,255,0.5); font-size: 13px; }
  .header .stats { display: flex; gap: 24px; }
  .stat { text-align: center; }
  .stat-value { color: white; font-size: 24px; font-weight: 700; }
  .stat-label { color: rgba(255,255,255,0.5); font-size: 11px; text-transform: uppercase; }

  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }

  table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  th { background: var(--dark); color: var(--gold); padding: 12px 16px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  td { padding: 14px 16px; border-bottom: 1px solid #eee; font-size: 14px; }
  tr:hover { background: #fafafa; }

  .badge {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
  }
  .badge-qualified { background: #d4edda; color: #155724; }
  .badge-nurture { background: #fff3cd; color: #856404; }
  .badge-redirect { background: #f8d7da; color: #721c24; }

  .score { font-weight: 700; font-size: 16px; }
  .score-high { color: var(--green); }
  .score-mid { color: var(--yellow); }
  .score-low { color: var(--red); }

  .empty { text-align: center; padding: 64px; color: #999; }
  .refresh { color: rgba(255,255,255,0.7); background: none; border: 1px solid rgba(255,255,255,0.3); padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 13px; }
  .refresh:hover { background: rgba(255,255,255,0.1); }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>Westbrook & Associates</h1>
    <p>Lead Qualification Dashboard</p>
  </div>
  <div class="stats">
    <div class="stat">
      <div class="stat-value" id="totalLeads">{{ total }}</div>
      <div class="stat-label">Total Leads</div>
    </div>
    <div class="stat">
      <div class="stat-value" style="color: #27ae60;" id="qualifiedCount">{{ qualified }}</div>
      <div class="stat-label">Qualified</div>
    </div>
    <div class="stat">
      <div class="stat-value" style="color: #f39c12;" id="nurtureCount">{{ nurture }}</div>
      <div class="stat-label">Nurture</div>
    </div>
    <div class="stat">
      <div class="stat-value" style="color: #e74c3c;" id="redirectCount">{{ redirect }}</div>
      <div class="stat-label">Redirected</div>
    </div>
  </div>
  <button class="refresh" onclick="location.reload()">Refresh</button>
</div>
<div class="container">
  {% if leads %}
  <table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Name</th>
        <th>Case Type</th>
        <th>Score</th>
        <th>Routing</th>
        <th>Email</th>
        <th>Zip</th>
      </tr>
    </thead>
    <tbody>
      {% for lead in leads %}
      <tr>
        <td>{{ lead.created_at[:16] }}</td>
        <td><strong>{{ lead.caller_name }}</strong></td>
        <td>{{ lead.case_type or '—' }}</td>
        <td>
          <span class="score {% if lead.score >= 7 %}score-high{% elif lead.score >= 4 %}score-mid{% else %}score-low{% endif %}">
            {{ lead.score }}/10
          </span>
        </td>
        <td>
          <span class="badge badge-{{ lead.routing }}">
            {{ lead.routing | upper }}
          </span>
        </td>
        <td>{{ lead.email or '—' }}</td>
        <td>{{ lead.zip_code or '—' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="empty">
    <p style="font-size: 18px; margin-bottom: 8px;">No leads yet</p>
    <p>Call your Vapi number to generate the first lead.</p>
  </div>
  {% endif %}
</div>

<script>
  // Auto-refresh every 10 seconds
  setTimeout(() => location.reload(), 10000);
</script>
</body>
</html>
"""


@app.route("/")
@app.route("/dashboard")
def dashboard():
    db = get_db()
    leads = db.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()

    total = len(leads)
    qualified = sum(1 for l in leads if l["routing"] == "qualified")
    nurture_count = sum(1 for l in leads if l["routing"] == "nurture")
    redirect_count = sum(1 for l in leads if l["routing"] == "redirect")

    return render_template_string(
        DASHBOARD_HTML,
        leads=leads,
        total=total,
        qualified=qualified,
        nurture=nurture_count,
        redirect=redirect_count,
    )


# ─── API ───────────────────────────────────────────────────────────────────

@app.route("/api/leads")
def api_leads():
    db = get_db()
    leads = db.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    return jsonify([dict(l) for l in leads])


# ─── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("=" * 60)
    print("  Westbrook & Associates — Voice Agent Webhook Server")
    print("  Dashboard: http://localhost:5002")
    print("  Webhook:   http://localhost:5002/webhook/tools")
    print("=" * 60)
    app.run(debug=True, port=5002)
