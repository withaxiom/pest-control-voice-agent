"""
Vapi.ai Setup Script — Westbrook & Associates Voice Agent
Creates tools, assistant, and phone number via the Vapi API.

Usage:
  python vapi_setup.py setup     # Full setup (tools + assistant + phone number)
  python vapi_setup.py status    # Show current config
  python vapi_setup.py teardown  # Delete all created resources
"""

import os
import sys
import json

import requests
from dotenv import load_dotenv

load_dotenv()

VAPI_API_KEY = os.environ["VAPI_API_KEY"]
BASE_URL = "https://api.vapi.ai"
HEADERS = {
    "Authorization": f"Bearer {VAPI_API_KEY}",
    "Content-Type": "application/json",
}

CONFIG_FILE = "vapi_config.json"


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    print(f"Config saved to {CONFIG_FILE}")


def create_tool(name, description, parameters, server_url):
    resp = requests.post(
        f"{BASE_URL}/tool",
        headers=HEADERS,
        json={
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
            "server": {"url": server_url},
        },
    )
    resp.raise_for_status()
    tool = resp.json()
    print(f"  Created tool '{name}': {tool['id']}")
    return tool["id"]


def create_tools(server_url):
    print("Creating tools...")

    log_lead_id = create_tool(
        name="log_lead",
        description=(
            "Log a qualified lead to the CRM with their contact information, "
            "case details, and qualification score. Call this at the END of "
            "every conversation after routing is determined."
        ),
        parameters={
            "type": "object",
            "properties": {
                "caller_name": {
                    "type": "string",
                    "description": "Full name of the caller",
                },
                "case_type": {
                    "type": "string",
                    "description": "Type of legal matter (e.g. personal injury, family law, business litigation)",
                },
                "case_summary": {
                    "type": "string",
                    "description": "Brief summary of the caller's situation",
                },
                "score": {
                    "type": "integer",
                    "description": "Lead qualification score from 0-10",
                },
                "routing": {
                    "type": "string",
                    "enum": ["qualified", "nurture", "redirect"],
                    "description": "Routing decision based on score",
                },
                "email": {
                    "type": "string",
                    "description": "Caller's email address if collected",
                },
                "zip_code": {
                    "type": "string",
                    "description": "Caller's zip code",
                },
            },
            "required": ["caller_name", "score", "routing"],
        },
        server_url=f"{server_url}/webhook/tools",
    )

    check_avail_id = create_tool(
        name="check_availability",
        description=(
            "Check available consultation time slots for the next few business days. "
            "Use this when a qualified lead (score 7-10) wants to book a consultation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "case_type": {
                    "type": "string",
                    "description": "Type of legal matter to match with the right attorney",
                },
            },
            "required": ["case_type"],
        },
        server_url=f"{server_url}/webhook/tools",
    )

    send_email_id = create_tool(
        name="send_nurture_email",
        description=(
            "Send a follow-up email with legal resources to a nurture lead (score 4-6). "
            "Use this after capturing the caller's email address."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Caller's first name for personalization",
                },
                "email": {
                    "type": "string",
                    "description": "Caller's email address",
                },
                "case_type": {
                    "type": "string",
                    "description": "Type of legal matter for relevant resources",
                },
            },
            "required": ["name", "email", "case_type"],
        },
        server_url=f"{server_url}/webhook/tools",
    )

    transfer_id = create_tool(
        name="transfer_call",
        description=(
            "Transfer the call to a human receptionist. Use when the caller "
            "explicitly asks for a human, or after 3 failed attempts to understand them."
        ),
        parameters={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Reason for the transfer",
                },
            },
            "required": ["reason"],
        },
        server_url=f"{server_url}/webhook/tools",
    )

    return {
        "log_lead": log_lead_id,
        "check_availability": check_avail_id,
        "send_nurture_email": send_email_id,
        "transfer_call": transfer_id,
    }


def create_assistant(tool_ids, server_url):
    print("Creating assistant...")

    with open("prompts/system_prompt.txt") as f:
        system_prompt = f.read()

    resp = requests.post(
        f"{BASE_URL}/assistant",
        headers=HEADERS,
        json={
            "name": "Westbrook Lead Qualifier — Alex",
            "firstMessage": (
                "Thank you for calling Westbrook and Associates. "
                "My name is Alex, and I'm here to help connect you with the right attorney. "
                "May I ask who I'm speaking with?"
            ),
            "model": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-20250514",
                "messages": [
                    {"role": "system", "content": system_prompt}
                ],
                "temperature": 0.7,
                "maxTokens": 300,
                "toolIds": list(tool_ids.values()),
            },
            "voice": {
                "provider": "11labs",
                "voiceId": "TxGEqnHWrfWFTfGW9XjX",
                "stability": 0.5,
                "similarityBoost": 0.75,
            },
            "transcriber": {
                "provider": "deepgram",
                "model": "nova-2",
                "language": "en",
                "smartFormat": True,
            },
            "serverUrl": f"{server_url}/webhook/vapi",
            "recordingEnabled": True,
            "endCallMessage": "Thank you for calling Westbrook and Associates. Have a wonderful day.",
        },
    )
    resp.raise_for_status()
    assistant = resp.json()
    print(f"  Created assistant: {assistant['id']}")
    return assistant["id"]


def create_phone_number(assistant_id):
    print("Creating phone number...")

    resp = requests.post(
        f"{BASE_URL}/phone-number",
        headers=HEADERS,
        json={
            "provider": "vapi",
            "numberDesiredAreaCode": "830",
            "name": "Westbrook & Associates Main Line",
            "assistantId": assistant_id,
        },
    )
    resp.raise_for_status()
    phone = resp.json()
    print(f"  Phone number: {phone['number']}")
    return {"id": phone["id"], "number": phone["number"]}


def setup():
    server_url = input("Enter your public webhook URL (e.g. https://abc123.ngrok.io): ").strip()
    if not server_url:
        print("Error: webhook URL required. Use ngrok to expose your local server.")
        sys.exit(1)

    tool_ids = create_tools(server_url)
    assistant_id = create_assistant(tool_ids, server_url)
    phone = create_phone_number(assistant_id)

    config = {
        "tool_ids": tool_ids,
        "assistant_id": assistant_id,
        "phone_number_id": phone["id"],
        "phone_number": phone["number"],
        "server_url": server_url,
    }
    save_config(config)

    print("\n" + "=" * 60)
    print("  Setup complete!")
    print(f"  Phone number: {phone['number']}")
    print(f"  Assistant ID: {assistant_id}")
    print("  Start your webhook server: python server.py")
    print("  Then call the number to test!")
    print("=" * 60)


def status():
    config = load_config()
    if not config:
        print("No config found. Run: python vapi_setup.py setup")
        return
    print(json.dumps(config, indent=2))


def teardown():
    config = load_config()
    if not config:
        print("No config found.")
        return

    confirm = input("Delete all Vapi resources? (yes/no): ").strip()
    if confirm != "yes":
        print("Cancelled.")
        return

    if "phone_number_id" in config:
        requests.delete(f"{BASE_URL}/phone-number/{config['phone_number_id']}", headers=HEADERS)
        print("  Deleted phone number")

    if "assistant_id" in config:
        requests.delete(f"{BASE_URL}/assistant/{config['assistant_id']}", headers=HEADERS)
        print("  Deleted assistant")

    for name, tool_id in config.get("tool_ids", {}).items():
        requests.delete(f"{BASE_URL}/tool/{tool_id}", headers=HEADERS)
        print(f"  Deleted tool: {name}")

    os.remove(CONFIG_FILE)
    print("Teardown complete.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python vapi_setup.py [setup|status|teardown]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "setup":
        setup()
    elif cmd == "status":
        status()
    elif cmd == "teardown":
        teardown()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
