"""
Orchestrator: a bounded tool-calling loop (planner -> tool -> observation ->
repeat) using Groq's OpenAI-compatible chat completions API with native
function calling. No keyword matching, no single mega-prompt - the model
decides which tool(s) to call and in what order; the tools themselves are
deterministic Python (see eligibility.py).
"""
import json
import os
from groq import Groq

from . import tools
from .session import Session

_MODEL = os.environ.get("TRENDLY_MODEL", "openai/gpt-oss-120b")
_MAX_TOOL_ITERATIONS = 6  # failure-recovery bound: don't let the model loop forever

_POLICY_PATH = os.path.join(os.path.dirname(__file__), "data", "trendly_policy.md")
with open(_POLICY_PATH, "r", encoding="utf-8") as f:
    _POLICY_TEXT = f.read()

_client = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _client


SYSTEM_PROMPT = f"""You are Trendly's customer support assistant, embedded as a chat widget on the Trendly website.

# Ground rules (non-negotiable)
- The policy document below (delimited by ===POLICY===) is your ONLY source of truth for shipping/returns/refund/exchange questions. If something isn't covered there, say you don't know and call escalate_to_human - never invent policy.
- Never discuss or confirm any order that doesn't belong to the identified customer. If identify_customer hasn't succeeded yet, ask for their email or phone before looking up anything order-specific.
- Never offer discounts, coupons, or goodwill credit that aren't explicitly defined in the policy.
- Never ask for or accept bank account numbers, card numbers, or CVV in chat. COD refunds require a human agent over a secure link (3.3) - escalate instead.
- For lost-parcel orders (status lost_in_transit, or policy 1.6), do NOT try to process a return - escalate to a human immediately.
- Always call check_return_eligibility or check_exchange_eligibility BEFORE telling a customer whether something is eligible, and before calling initiate_return/initiate_exchange. Do not compute eligibility yourself from the raw dates - use the tool.
- If a customer sounds upset (e.g. a delayed or lost order), acknowledge that briefly and genuinely before diving into policy/process. Don't be robotic about it, but don't over-apologize either.
- Keep replies concise and in plain language - this is a chat widget, not an email.
- When escalating, write a summary a human agent could act on immediately (what happened, what you already checked/ruled out) - never just "customer is upset, please help."

===POLICY===
{_POLICY_TEXT}
===END POLICY===
"""


def _tool_result_message(tool_call_id: str, name: str, content: dict) -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": json.dumps(content)}


def handle_message(session: Session, user_text: str) -> str:
    if not session.messages:
        session.messages.append({"role": "system", "content": SYSTEM_PROMPT})

    session.messages.append({"role": "user", "content": user_text})

    client = _get_client()

    for _ in range(_MAX_TOOL_ITERATIONS):
        response = client.chat.completions.create(
            model=_MODEL,
            messages=session.messages,
            tools=tools.TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0.2,
        )
        msg = response.choices[0].message
        session.messages.append({"role": "assistant", "content": msg.content, "tool_calls": msg.tool_calls})

        if not msg.tool_calls:
            return msg.content or "Sorry, I didn't catch that - could you rephrase?"

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            executor = tools.TOOL_EXECUTORS.get(name)
            if not executor:
                result = {"ok": False, "error": f"Unknown tool {name}"}
            else:
                try:
                    result = executor(args, session)
                except Exception as e:  # failure recovery: never crash the conversation
                    result = {"ok": False, "error": f"Internal error running {name}: {e}"}

            session.messages.append(_tool_result_message(tc.id, name, result))

    # bound hit - don't loop forever, degrade to an honest message + escalation
    return ("I'm having trouble finishing that request automatically. Let me get a human agent to take it from "
            "here so you're not stuck.")
