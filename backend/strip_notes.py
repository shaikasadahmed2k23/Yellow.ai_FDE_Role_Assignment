import json
import os

BASE = os.path.dirname(__file__)
SRC = os.path.join(BASE, "app", "data", "orders.json")
DST = os.path.join(BASE, "app", "data", "orders_runtime.json")

with open(SRC, "r", encoding="utf-8") as f:
    orders = json.load(f)

def strip_notes(obj):
    if isinstance(obj, dict):
        obj.pop("_note_for_designers", None)
        for v in obj.values():
            strip_notes(v)
    elif isinstance(obj, list):
        for item in obj:
            strip_notes(item)

strip_notes(orders)

with open(DST, "w", encoding="utf-8") as f:
    json.dump(orders, f, indent=2)

print(f"Stripped. Saved to {DST}")