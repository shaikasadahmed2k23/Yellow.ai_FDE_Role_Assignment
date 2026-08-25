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

import re

_MODEL = os.environ.get("TRENDLY_MODEL", "openai/gpt-oss-120b")
_MAX_TOOL_ITERATIONS = 6  # failure-recovery bound: don't let the model loop forever

# Safety net: catches the model stating an eligibility verdict without having
# called the deterministic eligibility tool first (a real failure mode we
# observed with gpt-oss-120b - it sometimes "knows" the policy well enough to
# answer plausibly without actually checking). This is a backstop, not the
# primary control - RULE 1 in the system prompt is the primary control.
_ELIGIBILITY_LANGUAGE = re.compile(
    r"\b(not\s+eligible|is\s+eligible|can(?:not|'t)?\s+be\s+(?:returned|exchanged)|"
    r"return\s+window|exchange\s+window|days?\s+(?:left|remaining|since\s+delivery)|"
    r"non-returnable|final\s+sale|past\s+(?:its|the)\s+expected\s+delivery)\b",
    re.IGNORECASE,
)
_ELIGIBILITY_TOOL_NAMES = {"check_return_eligibility", "check_exchange_eligibility"}


_ELIGIBILITY_TOOL_NAMES = {
    "check_return_eligibility",
    "check_exchange_eligibility",
    "check_delay_credit",
    "is_lost_parcel",
}


def _eligibility_tool_was_called(messages: list[dict]) -> bool:
    """True if any eligibility/verdict-producing tool appears anywhere in this
    session's tool-call history (not just this turn) - covers cases where
    eligibility was established in an earlier turn."""
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                if tc["function"]["name"] in _ELIGIBILITY_TOOL_NAMES:
                    return True
    return False
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

## RULE 1 - NEVER STATE ELIGIBILITY WITHOUT A TOOL CALL
Before you say ANY of the following about ANY item, you MUST have already called check_return_eligibility or check_exchange_eligibility for that exact SKU in this conversation, and your statement must match what the tool returned:
- "eligible" / "not eligible" / "can be returned" / "cannot be returned" / "can be exchanged"
- any reference to days since delivery, days remaining, or "the window has closed"
- any category-based refusal (e.g. "this is non-returnable")
This applies even when the answer feels obvious to you (e.g. innerwear, jewellery). If a customer asks about return/exchange for multiple items in one message, call the tool once per item before responding to any of them. WRONG example: "Ankle Socks can't be returned (non-returnable category), and the tee is past the 30-day window" written without calling check_return_eligibility for the tee first - this is a hard violation even if the conclusion is correct.

## RULE 2 - ACKNOWLEDGE FRUSTRATION FIRST, EVERY TIME
If the customer's message expresses frustration, disappointment, urgency, or negative sentiment (e.g. "this is frustrating", "very late", "unacceptable", "I've been waiting"), your reply MUST open with one brief, genuine acknowledgment sentence BEFORE any tool-result, policy detail, or resolution. Do not lead with "Got it" followed immediately by the fix - that reads as robotic. Do not skip this even if the fix is fast and simple.

- The policy document below (delimited by ===POLICY===) is your ONLY source of truth for shipping/returns/refund/exchange questions. If something isn't covered there, say you don't know and call escalate_to_human - never invent policy.
- Never discuss or confirm any order that doesn't belong to the identified customer. If identify_customer hasn't succeeded yet, ask for their email or phone before looking up anything order-specific.
- Never offer discounts, coupons, or goodwill credit that aren't explicitly defined in the policy.
- Never ask for or accept bank account numbers, card numbers, or CVV in chat. COD refunds require a human agent over a secure link (3.3) - escalate instead.
- For lost-parcel orders (status lost_in_transit, or policy 1.6), do NOT try to process a return - escalate to a human immediately.
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
    safety_net_retried = False

    for _ in range(_MAX_TOOL_ITERATIONS):
        try:
            response = client.chat.completions.create(
                model=_MODEL,
                messages=session.messages,
                tools=tools.TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=0.1,
            )
        except Exception as e:
            # Groq can reject a malformed tool call (e.g. the model passing
            # null for an optional string field) with a 400 that would
            # otherwise crash the request. Never let a provider-side
            # validation error surface as a 500 to the customer.
            print(f"[LLM CALL ERROR] {e}")
            return ("I'm having trouble processing that right now. Let me get a human agent to help you instead - "
                    "they'll follow up shortly.")
        msg = response.choices[0].message

        # msg.tool_calls are SDK objects, not plain dicts - must convert before
        # storing, since the full messages list gets JSON-serialized on every
        # subsequent API call (this is what a naive `msg.tool_calls` passthrough breaks).
        tool_calls_payload = None
        if msg.tool_calls:
            tool_calls_payload = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]

        assistant_turn = {"role": "assistant", "content": msg.content}
        if tool_calls_payload:
            assistant_turn["tool_calls"] = tool_calls_payload
        session.messages.append(assistant_turn)

        if not msg.tool_calls:
            content = msg.content or "Sorry, I didn't catch that - could you rephrase?"

            mentions_specific_order = bool(re.search(r"\bTR-\d{4}\b", content))
            if mentions_specific_order and _ELIGIBILITY_LANGUAGE.search(content) and not _eligibility_tool_was_called(session.messages):                
                print(f"[SAFETY NET] Eligibility language detected with no eligibility tool call - forcing retry. Reply was: {content!r}")
                if not safety_net_retried:
                    safety_net_retried = True
                    session.messages.append({
                        "role": "user",
                        "content": (
                            "SYSTEM NOTICE: You stated an eligibility verdict without calling "
                            "check_return_eligibility, check_exchange_eligibility, check_delay_credit, "
                            "or is_lost_parcel. Call the correct tool for the item(s) in question now, "
                            "then answer again based on its result."
                        ),
                    })
                    continue
                else:
                    print("[SAFETY NET] Retry already attempted this turn - escalating instead of risking a second wrong answer.")
                    return (
                        "I want to double-check that against our system before confirming - let me get "
                        "a human agent to verify and follow up with you shortly."
                    )

            return content

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            print(f"[TOOL CALL] {name}({args})")

            executor = tools.TOOL_EXECUTORS.get(name)
            if not executor:
                result = {"ok": False, "error": f"Unknown tool {name}"}
            else:
                try:
                    result = executor(args, session)
                except Exception as e:  # failure recovery: never crash the conversation
                    result = {"ok": False, "error": f"Internal error running {name}: {e}"}

            print(f"[TOOL RESULT] {name} -> {result}")

            session.messages.append(_tool_result_message(tc.id, name, result))

    # bound hit - don't loop forever, degrade to an honest message + escalation
    return ("I'm having trouble finishing that request automatically. Let me get a human agent to take it from "
            "here so you're not stuck.")
