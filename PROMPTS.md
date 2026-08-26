# PROMPTS.md — Prompt engineering notes

This documents how the system prompt and tool-calling design in `agent.py` actually evolved — not a polished write-up after the fact, but what was tried, what broke, and why the final version looks the way it does. The full system prompt as shipped is at the bottom of this file, verbatim from the repo.

## Design philosophy

The core decision made early and never revisited: **the model orchestrates, it doesn't decide.** No policy fact, eligibility verdict, or refund amount is ever something the model computes or recalls from the prompt — those live in `eligibility.py` as plain Python. The system prompt's job is narrower than it might look: get the model to (a) call the right tool for the right thing, (b) never state a conclusion it hasn't actually verified via a tool result, and (c) phrase the outcome like a person, not a policy printout.

This shaped the prompt style throughout — rules are written as hard constraints with concrete violation examples, not vague guidance like "be accurate" or "follow policy." A free-tier model treated as capable of nuanced judgment calls turned out to need much more explicit, almost legalistic phrasing than a first draft assumed.

## Iteration 1 — the naive version

The first system prompt was short: identify the customer, use the tools, follow the policy doc, be helpful. This worked for clean single-item requests. It broke immediately on two fronts once real testing started:

1. **Multi-item requests.** Asked about returning two items in one message, the model would call the eligibility tool for one and just *state* an answer for the other — confidently, and often correctly, but without ever checking. Since `gpt-oss-120b` clearly has enough of the policy text in-context to produce a plausible-sounding answer on its own, "plausible" and "checked" became indistinguishable from the outside. That's a real risk for anything touching refunds.
2. **No empathy.** For a customer clearly frustrated about a two-week delay, the model would jump straight to "Here's your ₹250 credit" — technically correct, but tone-deaf in a way that would read badly on a real support surface.

Neither of these was a one-off — they reproduced consistently enough across phrasings to be worth fixing at the prompt level rather than shrugging off as model noise.

## Iteration 2 — RULE 1 and RULE 2

Added two explicit, numbered, non-negotiable rules at the very top of the prompt (position mattered — burying them further down noticeably reduced how reliably they were followed):

- **RULE 1** forbids stating *any* eligibility-adjacent claim — eligible, not eligible, days remaining, category refusal — without having already called the matching tool for that exact SKU in the conversation, and includes a concrete "WRONG example" showing the violation pattern rather than just describing it abstractly. Concrete negative examples measurably outperformed abstract instructions during testing.
- **RULE 2** requires a genuine one-sentence acknowledgment before any tool result or fix, whenever the customer's message carries frustration, urgency, or negative sentiment — explicitly banning the "Got it — here's the fix" pattern, which is a common but robotic failure mode.

This closed most of the gap but not all of it — RULE 1 reduced the frequency of unverified verdicts significantly but didn't eliminate them completely on every phrasing, which is what motivated the next layer.

## Iteration 3 — the safety net (prompting alone wasn't enough)

Since RULE 1 was a prompt-level instruction and the model is non-deterministic, testing kept finding edge cases where it still slipped through — especially on emotionally loaded phrasing where the model seemed to prioritize sounding reassuring over calling the tool first. Rather than keep tuning prompt wording indefinitely, a server-side regex safety net was added in `agent.py`: if the model's final reply references a real order ID and uses eligibility-adjacent language, but no matching tool call exists anywhere in that session's history, the system silently injects a corrective system notice and forces one retry — and if that retry also fails, it degrades to an honest "let me get a human to verify" rather than risking a second unverified answer reaching the customer.

This is deliberately positioned as a **backstop, not the primary control** — the comment in the code says exactly that. The primary control is still RULE 1; the safety net exists because "the prompt says so" isn't a sufficient guarantee on its own when the cost of being wrong is a real refund decision.

Two follow-on bugs surfaced from the safety net itself, both fixed at the code level rather than the prompt level (see `SOLUTION.md`'s debugging journey for the full detail): it initially false-triggered on general, order-less policy explanations (fixed by only enforcing the check when the reply names a real `TR-####` order), and it initially only recognized two of the four eligibility-producing tools as valid evidence, incorrectly flagging correct delay-credit and lost-parcel answers too.

## Other prompt-level decisions

- **Temperature 0.1**, lowered from an initial 0.2, purely for consistency of rule-following — this isn't a creative-writing task, and the lower temperature measurably reduced how often RULE 1 got skipped without any noticeable cost to reply quality.
- **Full policy document embedded directly in the system prompt**, delimited by explicit `===POLICY===` / `===END POLICY===` markers, rather than RAG. The policy doc is short enough that retrieval would add failure surface (chunk boundaries, retrieval misses) without adding real benefit — documented as a trade-off in `SOLUTION.md` that would need revisiting if the policy doc grew substantially.
- **Explicit prohibition on inventing policy** — if something isn't in the embedded doc, the model is told to say so and escalate rather than guess. This came from a general instinct about free-tier models filling gaps confidently rather than a single reproduced incident, but was worth stating as its own rule rather than assuming it followed from "use the policy doc."
- **Never ask for or accept bank details in chat** — a hard-coded refusal independent of anything the policy doc says, since this is the one place where a plausible-sounding but wrong model response would be a real security problem, not just a wrong answer.
- **Lost-parcel handling kept structurally separate from the return flow** — the prompt explicitly tells the model not to attempt processing a `lost_in_transit` order as a return, since that's a claims/escalation situation, not a returns one, and the two are easy to conflate from the customer's phrasing alone ("it never arrived" sounds like it could be either).

## Tool design notes

`tools.py` exposes OpenAI-compatible function-calling schemas. Two choices worth calling out:

- `customer_id` is never a parameter the model supplies for any order-specific tool. Identity is resolved once via `identify_customer` from what the customer states conversationally, then read from session state for every later call in that conversation. This isn't a prompt instruction the model could ignore — it's structural: the tool executors simply don't accept a customer_id argument from the LLM at all, which is why cross-customer data leakage is prevented by the code's shape rather than by hoping the model behaves.
- `escalate_to_human`'s optional `order_id` parameter description explicitly tells the model to *omit* the field rather than pass `null` when there's no relevant order — added after Groq's strict schema validator was found to reject a literal `null` outright, crashing the request. A one-line schema description fixed what looked at first like a code bug.

## Full system prompt (as shipped)

```
You are Trendly's customer support assistant, embedded as a chat widget on the Trendly website.

# Ground rules (non-negotiable)

## RULE 1 - NEVER STATE ELIGIBILITY WITHOUT A TOOL CALL
Before you say ANY of the following about ANY item, you MUST have already called
check_return_eligibility or check_exchange_eligibility for that exact SKU in this
conversation, and your statement must match what the tool returned:
- "eligible" / "not eligible" / "can be returned" / "cannot be returned" / "can be exchanged"
- any reference to days since delivery, days remaining, or "the window has closed"
- any category-based refusal (e.g. "this is non-returnable")
This applies even when the answer feels obvious to you (e.g. innerwear, jewellery). If a
customer asks about return/exchange for multiple items in one message, call the tool once
per item before responding to any of them. WRONG example: "Ankle Socks can't be returned
(non-returnable category), and the tee is past the 30-day window" written without calling
check_return_eligibility for the tee first - this is a hard violation even if the
conclusion is correct.

## RULE 2 - ACKNOWLEDGE FRUSTRATION FIRST, EVERY TIME
If the customer's message expresses frustration, disappointment, urgency, or negative
sentiment (e.g. "this is frustrating", "very late", "unacceptable", "I've been waiting"),
your reply MUST open with one brief, genuine acknowledgment sentence BEFORE any
tool-result, policy detail, or resolution. Do not lead with "Got it" followed immediately
by the fix - that reads as robotic. Do not skip this even if the fix is fast and simple.

- The policy document below (delimited by ===POLICY===) is your ONLY source of truth for
  shipping/returns/refund/exchange questions. If something isn't covered there, say you
  don't know and call escalate_to_human - never invent policy.
- Never discuss or confirm any order that doesn't belong to the identified customer. If
  identify_customer hasn't succeeded yet, ask for their email or phone before looking up
  anything order-specific.
- Never offer discounts, coupons, or goodwill credit that aren't explicitly defined in
  the policy.
- Never ask for or accept bank account numbers, card numbers, or CVV in chat. COD refunds
  require a human agent over a secure link (3.3) - escalate instead.
- For lost-parcel orders (status lost_in_transit, or policy 1.6), do NOT try to process a
  return - escalate to a human immediately.
- Keep replies concise and in plain language - this is a chat widget, not an email.
- When escalating, write a summary a human agent could act on immediately (what happened,
  what you already checked/ruled out) - never just "customer is upset, please help."

===POLICY===
{full Trendly policy document text, loaded at runtime from data/trendly_policy.md}
===END POLICY===
```

**Runtime parameters:** `model=openai/gpt-oss-120b`, `temperature=0.1`, `tool_choice="auto"`, max 6 tool-calling iterations per turn (a failure-recovery bound, not a normal-case limit — most turns resolve in 1-2 iterations).
