# Test fixtures

`sample.csv` — 100 rows of synthetic clinical-shaped data used by every contract test and the E2E smoke test. Columns cover the type shapes the app has to handle:

| Column       | Type       | Notes                                  |
|--------------|------------|----------------------------------------|
| subject_id   | integer    | 1–100, unique                          |
| age          | integer    | 18–85                                  |
| weight_kg    | float      | ~8% NaN values (exercises missing)     |
| treatment    | categorical string | 5 distinct values (Placebo, DrugA–C, Control) |
| notes        | free-text string | low-cardinality but not categorical |
| visit_date   | ISO date string | 2024-01 through 2024-04           |
| active_fl    | boolean-ish | Y / N                                 |

The file is committed, seeded, and deterministic. If you need to regenerate it, run `python _generate_sample.py` from this directory. Do not edit the CSV by hand — tests assert against specific counts (row count, category cardinality, missing-value count).
