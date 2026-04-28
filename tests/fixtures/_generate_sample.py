"""Regenerate tests/fixtures/sample.csv.

Deterministic (seeded). Run manually if you ever need to regenerate the fixture.
The committed sample.csv is what tests actually load — this script exists so
future engineers can reproduce it, not so tests call it at runtime.
"""
import csv
import random
from pathlib import Path

random.seed(42)

CATEGORIES = ["Placebo", "DrugA", "DrugB", "DrugC", "Control"]
NOTES = [
    "patient tolerated well",
    "mild headache reported",
    "no adverse events",
    "completed all visits",
    "withdrew due to scheduling",
    "minor nausea noted",
]
START_DAY = 1
ROW_COUNT = 100

rows = []
for i in range(1, ROW_COUNT + 1):
    age = random.randint(18, 85)
    # weight has ~8% missing to exercise NaN handling
    weight = "" if random.random() < 0.08 else round(random.uniform(45.0, 120.0), 1)
    treatment = random.choice(CATEGORIES)
    note = random.choice(NOTES)
    # ISO date spread across Jan-Apr 2024
    month = random.choice([1, 2, 3, 4])
    day = random.randint(1, 28)
    visit_date = f"2024-{month:02d}-{day:02d}"
    active = random.choice(["Y", "N"])
    rows.append({
        "subject_id": i,
        "age": age,
        "weight_kg": weight,
        "treatment": treatment,
        "notes": note,
        "visit_date": visit_date,
        "active_fl": active,
    })

out = Path(__file__).parent / "sample.csv"
with out.open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)

print(f"Wrote {len(rows)} rows to {out}")
