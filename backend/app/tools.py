"""
Tools available to the agent, as OpenAI-compatible function-calling schemas,
plus their executors.

Security note: customer_id is NEVER an LLM-supplied argument. It's resolved
once via identify_customer and stored in session state; every order-specific
tool reads it from session, not from the model's output, and cross-checks
ownership via data_store.order_belongs_to_customer(). This is what prevents
cross-customer data leakage even if the model is prompted/tricked into
requesting someone else's order_id.
"""
from . import data_store as ds
from . import eligibility as elig
from . import actions

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "identify_customer",
            "description": "Resolve the customer's identity from their email or phone number. Must be called before any order-specific tool if the customer isn't already identified in this session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "Customer's email address, if given"},
                    "phone": {"type": "string", "description": "Customer's phone number, if given"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_my_orders",
            "description": "List order summaries (id, status, items, total) for the currently identified customer. Requires identify_customer to have succeeded first.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Get full details and a plain-language status explanation for a specific order. Only works if the order belongs to the identified customer.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string", "description": "e.g. TR-4521"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_return_eligibility",
            "description": "Deterministically check whether a specific item on an order can be returned, per policy sections 2.1-2.6. Always call this before telling a customer whether they can return something.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "sku": {"type": "string", "description": "The item SKU to check"},
                },
                "required": ["order_id", "sku"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_exchange_eligibility",
            "description": "Deterministically check whether a specific item is eligible for a size exchange, per policy section 4. Always call this before confirming an exchange.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}, "sku": {"type": "string"}},
                "required": ["order_id", "sku"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_delay_credit",
            "description": "Check whether an undelivered order qualifies for the ₹250 delay store credit per policy 1.5 (more than 3 business days past expected delivery).",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "initiate_return",
            "description": "Actually create a return request for an item, AFTER check_return_eligibility has confirmed it's eligible. Idempotent - calling this twice for the same item returns the existing request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "sku": {"type": "string"},
                    "reason": {"type": "string", "description": "Customer's stated reason for the return"},
                },
                "required": ["order_id", "sku", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "initiate_exchange",
            "description": "Actually create a size-exchange request, AFTER check_exchange_eligibility has confirmed eligibility. Idempotent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "sku": {"type": "string"},
                    "new_size": {"type": "string"},
                },
                "required": ["order_id", "sku", "new_size"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Hand off to a human support agent. Use for: lost parcels, COD refund bank details, damaged/wrong item reports needing photo review, second exchange on the same item, or anything the policy doc doesn't cover. Produces a structured ticket a human can act on immediately - write a real summary, not the raw transcript.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "Omit if not order-specific"},
                    "reason": {"type": "string", "description": "Short category, e.g. 'lost_parcel', 'cod_bank_details', 'damaged_item', 'out_of_policy'"},
                    "summary": {"type": "string", "description": "2-3 sentence summary a human agent can act on immediately, including what's already been checked/ruled out"},
                    "priority": {"type": "string", "enum": ["normal", "high"]},
                },
                "required": ["reason", "summary"],
            },
        },
    },
]


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def exec_identify_customer(args: dict, session) -> dict:
    customer = ds.find_customer_by_contact(email=args.get("email"), phone=args.get("phone"))
    if not customer:
        return _err("No customer found matching that email/phone. Ask them to double-check it.")
    session.customer_id = customer["customer_id"]
    session.customer_name = customer["name"]
    return {"ok": True, "customer_id": customer["customer_id"], "name": customer["name"]}


def exec_list_my_orders(args: dict, session) -> dict:
    if not session.customer_id:
        return _err("No customer identified yet - call identify_customer first.")
    orders = ds.get_orders_for_customer(session.customer_id)
    return {
        "ok": True,
        "orders": [
            {"order_id": o["order_id"], "status": o["status"], "placed_at": o["placed_at"],
             "items": [i["name"] for i in o["items"]], "total": o["total"]}
            for o in orders
        ],
    }


def exec_lookup_order(args: dict, session) -> dict:
    if not session.customer_id:
        return _err("No customer identified yet - call identify_customer first.")
    order_id = args["order_id"].strip().upper()
    if not ds.order_belongs_to_customer(order_id, session.customer_id):
        return _err("That order doesn't belong to the identified customer, or doesn't exist. Do not reveal any details about it.")
    session.active_order_id = order_id
    order = ds.get_order(order_id)
    return {"ok": True, "order": order, "existing_actions": actions.all_actions_for_order(order_id)}


def exec_check_return_eligibility(args: dict, session) -> dict:
    if not session.customer_id or not ds.order_belongs_to_customer(args["order_id"], session.customer_id):
        return _err("Order not found for this customer.")
    return {"ok": True, "verdict": elig.check_return_eligibility(args["order_id"], args["sku"]).to_dict()}


def exec_check_exchange_eligibility(args: dict, session) -> dict:
    if not session.customer_id or not ds.order_belongs_to_customer(args["order_id"], session.customer_id):
        return _err("Order not found for this customer.")
    return {"ok": True, "verdict": elig.check_exchange_eligibility(args["order_id"], args["sku"]).to_dict()}


def exec_check_delay_credit(args: dict, session) -> dict:
    if not session.customer_id or not ds.order_belongs_to_customer(args["order_id"], session.customer_id):
        return _err("Order not found for this customer.")
    return {"ok": True, "verdict": elig.check_delay_credit(args["order_id"]).to_dict()}


def exec_initiate_return(args: dict, session) -> dict:
    order_id, sku = args["order_id"], args["sku"]
    if not session.customer_id or not ds.order_belongs_to_customer(order_id, session.customer_id):
        return _err("Order not found for this customer.")

    existing = actions.find_open_action(order_id, sku, "return")
    if existing:
        return {"ok": True, "already_exists": True, "action": existing}

    verdict = elig.check_return_eligibility(order_id, sku)
    if not verdict.eligible:
        return _err(f"Cannot initiate return - not eligible: {verdict.reason}")

    order = ds.get_order(order_id)
    pincode_note = "Free reverse pickup will be scheduled (5.1)." if order["shipping_city"] else "Self-ship required; up to ₹150 courier reimbursement against receipt (5.2)."
    action = actions.create_action(order_id, sku, "return", reason=args.get("reason", ""), note=pincode_note)
    return {"ok": True, "action": action, "verdict": verdict.to_dict()}


def exec_initiate_exchange(args: dict, session) -> dict:
    order_id, sku = args["order_id"], args["sku"]
    if not session.customer_id or not ds.order_belongs_to_customer(order_id, session.customer_id):
        return _err("Order not found for this customer.")

    existing = actions.find_open_action(order_id, sku, "exchange")
    if existing:
        return {"ok": True, "already_exists": True, "action": existing}

    prior_exchanges = [a for a in actions.all_actions_for_order(order_id) if a["sku"] == sku and a["type"] == "exchange"]
    if prior_exchanges:
        return _err("This item already had one exchange - a second exchange needs human approval (4.4). Escalate instead.")

    verdict = elig.check_exchange_eligibility(order_id, sku)
    if not verdict.eligible:
        return _err(f"Cannot initiate exchange - not eligible: {verdict.reason}")

    action = actions.create_action(order_id, sku, "exchange", new_size=args.get("new_size"))
    return {"ok": True, "action": action, "verdict": verdict.to_dict()}


def exec_escalate_to_human(args: dict, session) -> dict:
    ticket = actions.create_escalation(
        order_id=args.get("order_id") or session.active_order_id,
        reason=args["reason"],
        summary=args["summary"],
        priority=args.get("priority", "normal"),
    )
    return {"ok": True, "ticket": ticket}


TOOL_EXECUTORS = {
    "identify_customer": exec_identify_customer,
    "list_my_orders": exec_list_my_orders,
    "lookup_order": exec_lookup_order,
    "check_return_eligibility": exec_check_return_eligibility,
    "check_exchange_eligibility": exec_check_exchange_eligibility,
    "check_delay_credit": exec_check_delay_credit,
    "initiate_return": exec_initiate_return,
    "initiate_exchange": exec_initiate_exchange,
    "escalate_to_human": exec_escalate_to_human,
}
