"""
Automated eval suite for eligibility.py — the deterministic policy engine.

This formalizes every scenario that was manually verified via the /chat
endpoint during development into fast, free, deterministic assertions with
no LLM call involved (eligibility.py never calls an LLM by design — see its
own module docstring). Run this before every deploy.

Each test is keyed to a specific fixed order in orders_runtime.json and
mirrors the _note_for_designers hint that originally shipped with that
order in orders.json (that field is stripped from the runtime copy the app
actually loads, but the expected outcome it describes is what's asserted
here).

Run from the backend/ directory:
    pytest -v
"""
from app import eligibility as elig
from app import data_store as ds


# ---------- TR-4521: in transit, not yet delivered ----------

def test_tr4521_not_delivered_return_ineligible():
    v = elig.check_return_eligibility("TR-4521", "TR-DRS-014")
    assert v.eligible is False
    assert v.rule_ref == "2.1"


# ---------- TR-4522: mixed apparel (returnable) + innerwear (non-returnable) ----------

def test_tr4522_tee_is_returnable():
    v = elig.check_return_eligibility("TR-4522", "TR-TSH-002")
    assert v.eligible is True
    assert v.rule_ref == "2.1"


def test_tr4522_socks_non_returnable_category():
    v = elig.check_return_eligibility("TR-4522", "TR-SOK-031")
    assert v.eligible is False
    assert v.rule_ref == "2.3"


# ---------- TR-4523: delivered well outside the 30-day window ----------

def test_tr4523_return_window_closed():
    v = elig.check_return_eligibility("TR-4523", "TR-JKT-008")
    assert v.eligible is False
    assert v.rule_ref == "2.1"
    assert v.extra["days_since_delivery"] > 30


# ---------- TR-4524: partially shipped, nothing delivered yet ----------

def test_tr4524_partial_shipment_return_ineligible():
    v = elig.check_return_eligibility("TR-4524", "TR-JNS-021")
    assert v.eligible is False
    assert v.rule_ref == "2.1"


# ---------- TR-4525: delayed past the delay-credit threshold ----------

def test_tr4525_delay_credit_eligible():
    v = elig.check_delay_credit("TR-4525")
    assert v.eligible is True
    assert v.rule_ref == "1.5"
    assert v.extra["credit_amount"] == 250
    assert v.extra["days_late"] == 14


# ---------- TR-4526: lost in transit ----------

def test_tr4526_lost_parcel_flagged():
    v = elig.is_lost_parcel("TR-4526")
    assert v.eligible is True
    assert v.rule_ref == "1.6"


def test_tr4526_not_flagged_for_unrelated_order():
    v = elig.is_lost_parcel("TR-4530")
    assert v.eligible is False


# ---------- TR-4527: jewellery, refused on category grounds not date grounds ----------

def test_tr4527_jewellery_non_returnable():
    v = elig.check_return_eligibility("TR-4527", "TR-EAR-042")
    assert v.eligible is False
    assert v.rule_ref == "2.3"


# ---------- TR-4528: final sale — return refused, exchange allowed ----------

def test_tr4528_final_sale_return_refused():
    v = elig.check_return_eligibility("TR-4528", "TR-SHR-009")
    assert v.eligible is False
    assert v.rule_ref == "2.4"


def test_tr4528_final_sale_exchange_allowed():
    v = elig.check_exchange_eligibility("TR-4528", "TR-SHR-009")
    assert v.eligible is True
    assert v.rule_ref == "4.1"


# ---------- TR-4529: already cancelled ----------

def test_tr4529_cancelled_order_return_refused():
    v = elig.check_return_eligibility("TR-4529", "TR-SCF-027")
    assert v.eligible is False
    assert v.rule_ref == "2.6"


# ---------- TR-4530: clean happy path ----------

def test_tr4530_happy_path_return_eligible():
    v = elig.check_return_eligibility("TR-4530", "TR-KRT-033")
    assert v.eligible is True
    assert v.rule_ref == "2.1"


def test_tr4530_delivered_order_no_delay_credit():
    v = elig.check_delay_credit("TR-4530")
    assert v.eligible is False


# ---------- negative / not-found cases ----------

def test_unknown_order_id():
    v = elig.check_return_eligibility("TR-9999", "SKU-X")
    assert v.eligible is False
    assert v.rule_ref == "n/a"


def test_unknown_sku_on_real_order():
    v = elig.check_return_eligibility("TR-4530", "SKU-DOES-NOT-EXIST")
    assert v.eligible is False
    assert v.rule_ref == "n/a"


# ---------- anti-leakage: the data layer must enforce ownership ----------
# This is the automated regression test for the "no data leakage"
# requirement — it doesn't touch the LLM at all, so it can't be affected by
# model variance. If this ever fails, cross-customer leakage is possible.

def test_order_belongs_to_correct_customer():
    assert ds.order_belongs_to_customer("TR-4522", "C-101") is True


def test_order_does_not_belong_to_wrong_customer():
    assert ds.order_belongs_to_customer("TR-4522", "C-100") is False


def test_nonexistent_order_belongs_to_nobody():
    assert ds.order_belongs_to_customer("TR-9999", "C-100") is False