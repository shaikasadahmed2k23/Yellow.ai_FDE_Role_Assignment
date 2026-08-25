# SOLUTION.md — Trendly Support Assistant

## Architecture

The system is a single FastAPI service exposing one primary endpoint, `POST /chat`, backed by a bounded ReAct tool-calling loop against Groq (`openai/gpt-oss-120b`, free tier). The core design principle: **policy decisions are computed by deterministic Python, never by the LLM.** The model's job is limited to orchestration — deciding which tool to call, in what order, and how to phrase the result — not to computing whether a return is eligible.

**Layers:**

- **`data_store.py`** — read-only access to the fixed order/customer dataset. Identity resolution (email/phone → `customer_id`) is the single entry point for all order access; `order_belongs_to_customer()` is the ownership check every order-specific tool must pass through.
- **`eligibility.py`** — the actual policy engine. Pure Python, no LLM calls, one function per policy area (`check_return_eligibility`, `check_exchange_eligibility`, `check_delay_credit`, `is_lost_parcel`), each mapped to a specific rule number in the policy doc. Date math is anchored to a fixed `SIMULATED_NOW` (2026-07-29) rather than wall-clock time, because the fixed dataset's expected outcomes only hold against that reference date.
- **`tools.py`** — OpenAI-compatible function-calling schemas plus executors. `customer_id` is never an LLM-supplied argument — it's resolved once via `identify_customer` and read from session state on every subsequent call, which is what makes cross-customer data leakage structurally impossible rather than merely prompted-against.
- **`actions.py`** — an in-memory, idempotent action log for returns, exchanges, and escalation tickets (calling `initiate_return` twice for the same item returns the existing request rather than duplicating it).
- **`agent.py`** — the tool-calling loop itself (max 6 iterations, failure-recovery bounded), with the full policy document embedded directly in the system prompt (no RAG — the doc is short enough that retrieval would add failure surface without benefit).
- **`main.py`** — FastAPI routes: `/chat`, `/health`, `/`, and a key-gated `/debug/escalations` for demo/grading visibility into the ticket log.
- **Frontend** — a single self-contained HTML/CSS/JS chat widget, no build step, deployed as a static file separate from the backend (CORS-enabled).

## Key trade-offs

**Deterministic eligibility engine vs. LLM-computed eligibility.** Policy logic lives in testable, auditable Python rather than being inferred by the model at request time. This costs more upfront engineering per rule and doesn't auto-adapt if the policy doc changes without a matching code change — but it means eligibility verdicts are provably correct rather than plausible-sounding, which matters far more for anything touching refunds.

**Full policy doc in-context vs. RAG.** Simpler, avoids retrieval failure modes, and the doc is small enough that this isn't wasteful. This won't scale cleanly if the policy document grows substantially — a real deployment would need to revisit this once the doc passes a certain size.

**In-memory state vs. persistent storage.** Sessions and the action log live in process memory, not a database. This was the right call for shipping inside the deadline, but it's a real production risk: any restart (including a free-tier host sleeping and waking) wipes all conversation and ticket state. Supabase was considered and deliberately deferred, not overlooked.

**Server-side safety net as a backstop to prompt instructions.** Testing surfaced two concrete cases where the model didn't follow its own system-prompt rules: stating a return-eligibility verdict without having called `check_return_eligibility`, and skipping an empathy acknowledgment before a fix. Rather than trusting the prompt alone, a regex-based check now inspects the model's final reply — if it references a specific order and uses eligibility language without a matching tool call in the session's history, the system forces one retry, then degrades to a human handoff rather than risking a second unverified answer. This is a heuristic backstop, not a formal guarantee, but it caught real violations during development and is documented as defense-in-depth, not the primary control (the primary control is still the sharpened system prompt).

**Single bounded ReAct loop vs. a multi-agent framework.** The task — order lookups plus policy application — doesn't need multi-agent decomposition. A planner/multi-agent architecture would add complexity and reduce auditability without adding real capability here.

## Known limitations

- **State doesn't survive a restart.** In-memory sessions and the action/ticket log reset on every deploy or host sleep cycle. Not production-viable as-is; the natural next step is Supabase or Redis-backed session storage.
- **The COD-refund-escalation path is only partially exercised by the fixed dataset.** Only two orders use `cash_on_delivery`, and neither has a live pending-refund scenario — one is outside the return window, the other is final-sale. The refusal-to-collect-bank-details behavior, the tool schema, and the system-prompt rule are all independently verified; the specific end-to-end "COD refund gets escalated" flow isn't, simply because the data doesn't produce that case.
- **`SIMULATED_NOW` is fixed, by design.** This keeps eligibility outcomes reproducible against the fixed dataset regardless of when the grader runs it, but it also means the live endpoint won't reflect real calendar time — a deliberate and documented choice, not an oversight.
- **The free-tier model's instruction-following isn't perfect.** Two real violations were caught during testing (see trade-offs above) and mitigated via a safety net, but that net is a heuristic, not a proof. A paid, more steerable model would likely need it less.
- **No automated tests exercise the LLM-in-the-loop behavior.** `tests/test_eligibility.py` covers the deterministic policy engine (19 tests, zero API cost, all passing) and the ownership/anti-leakage checks, but the agent's actual tool-selection and conversational behavior was verified through extensive manual, scripted testing during development rather than a repeatable CI suite — testing that would require either mocking the LLM or accepting cost and non-determinism in CI.
- **No formal rate limiting on `/chat`.** Message length is capped and empty messages are rejected, but there's no per-IP or per-session request throttling. Acceptable for a graded demo; a real deployment would need `slowapi` or a Redis-backed limiter.
- **`/debug/escalations` uses a single shared secret, not real per-user auth.** Fine for demo/grading visibility into ticket creation; not a production auth pattern.

## Five discovery questions for Trendly's ops team

1. **What actually happens for a COD refund today** — is there a defined SLA and process once the human agent picks it up, and would ops rather the agent collect an alternative refund method (e.g. a UPI ID) up front instead of only escalating?
2. **Is email/phone lookup the real identity model**, or is there an existing auth system (account login, OTP, order-confirmation-link) this should integrate with instead — and at what request volume does the current lookup approach start to strain?
3. **What's the source of truth for orders in production** — a live OMS/Shopify-style API rather than a static dataset — and what latency, rate limits, or auth constraints does that system impose?
4. **Is there an existing ticketing system** (Zendesk, Freshdesk, or similar) escalations should land in, rather than an internal log, and what fields does the human-agent team actually need in that handoff to act without re-asking the customer anything?
5. **How often does the policy document change, and who owns it?** This determines whether keeping the full policy text in the prompt remains the right call as it grows, or whether a versioned/RAG-based approach becomes worth the added complexity.