"""Build the small clinical sample used by the chat tool admin page's "Try it" preview.

Takes 36 subjects (12 per ARM) and about 20 columns of the CDISC pilot ADSL
(`datasets/adsl.sas7bdat`), chosen to cover every parameter type the data tools
take: numeric, categorical, date and id columns. Deterministic (fixed seed, sorted
output), so re-running produces a byte-identical file.

The output lives outside `datasets/` on purpose, so it never shows up in local
dataset discovery.

    uv run python scripts/build_chat_tool_preview_sample.py
"""
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "datasets" / "adsl.sas7bdat"
OUTPUT = REPO_ROOT / "backend" / "chat_tools" / "sample_data" / "adsl_preview.csv"

SUBJECTS_PER_ARM = 12
SEED = 20240611

NUMERIC = ["AGE", "BMIBL", "HEIGHTBL", "WEIGHTBL", "TRTDURD", "CUMDOSE"]
CATEGORICAL = ["ARM", "SEX", "RACE", "AGEGR1", "SITEID", "EOSSTT", "DCSREAS", "SAFFL", "ITTFL"]
DATES = ["TRTSDT", "TRTEDT"]
IDS = ["STUDYID", "USUBJID"]
COLUMNS = IDS + CATEGORICAL + NUMERIC + DATES


def build() -> pd.DataFrame:
    df = pd.read_sas(SOURCE)
    for column in df.columns:
        if df[column].dtype == object:
            df[column] = df[column].map(lambda v: v.decode("utf-8") if isinstance(v, bytes) else v)

    per_arm = [
        df[df["ARM"] == arm].sample(n=SUBJECTS_PER_ARM, random_state=SEED)
        for arm in sorted(df["ARM"].unique())
    ]
    sample = pd.concat(per_arm).sort_values("USUBJID")
    sample = sample[COLUMNS].reset_index(drop=True)
    for column in DATES:
        sample[column] = pd.to_datetime(sample[column]).dt.strftime("%Y-%m-%d")
    return sample


def main() -> None:
    sample = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    sample.to_csv(OUTPUT, index=False, lineterminator="\n")
    print(f"Wrote {len(sample)} rows x {len(sample.columns)} columns to {OUTPUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
