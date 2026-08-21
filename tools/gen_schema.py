"""
gen_schema.py
=============
Regenerates app/schema.py from scoring/sample_request.json.

The app's form, its API validation, and the model's preprocessor must agree on
37 column names in one exact order. Hand-maintaining that list in a second place
is how the UI silently starts sending reordered columns — which the endpoint
happily scores, returning a plausible number computed from the wrong features.
So the request contract is derived from the request file, not retyped.

    python tools/gen_schema.py

Assigning a new feature to a group is the only manual step; the asserts below
fail loudly if a feature is added to the model but not to a group, or vice versa.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scoring" / "sample_request.json"
DEST = ROOT / "app" / "schema.py"

GROUPS: list[tuple[str, str, list[str]]] = [
    ("Time & cycle", "Cyclical encodings and calendar position of the delivery block.",
     ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "block_sin", "block_cos",
      "day_of_month", "is_weekend", "is_peak_hour", "is_morning_ramp", "is_off_peak"]),
    ("Day-ahead market", "DAM bid/clearing volumes for the block.",
     ["dam_purchase_bid", "dam_sell_bid", "dam_mcv", "dam_volume", "dam_bid_imbalance"]),
    ("Real-time market", "RTM counterparts plus the DAM/RTM clearing-price spread.",
     ["rtm_purchase_bid", "rtm_sell_bid", "rtm_mcv", "rtm_volume", "mcp_spread"]),
    ("Price history", "Lagged and rolling DAM MCP. These dominate feature importance.",
     ["dam_mcp_lag_1d", "dam_mcp_lag_2d", "dam_mcp_lag_7d", "dam_mcp_roll_4h",
      "dam_mcp_roll_24h", "dam_mcp_roll_std_24h"]),
    ("Weather", "Load-weighted averages across the 66 station network.",
     ["avg_temp", "avg_humidity", "avg_windspeed", "avg_cloud_cover", "total_rainfall"]),
    ("Demand events", "Calendar drivers of anomalous demand.",
     ["is_ipl_match", "is_event", "is_festival", "is_wedding_season", "impact"]),
]

BINARY = {"is_weekend", "is_peak_hour", "is_morning_ramp", "is_off_peak",
          "is_ipl_match", "is_event", "is_festival", "is_wedding_season"}


def main() -> None:
    payload = json.loads(SRC.read_text(encoding="utf-8"))
    cols = payload["input_data"]["columns"]
    row = payload["input_data"]["data"][0]
    sample = dict(zip(cols, row))

    grouped = [f for _, _, fs in GROUPS for f in fs]
    missing = [c for c in cols if c not in grouped]
    extra = [f for f in grouped if f not in cols]
    if missing:
        raise SystemExit(f"features in the model but not assigned to a group: {missing}")
    if extra:
        raise SystemExit(f"grouped features that the model does not accept: {extra}")

    o = io.StringIO()
    o.write('"""\nschema.py\n=========\nThe model\'s input contract, in one place.\n\n')
    o.write("Generated from scoring/sample_request.json — the same 37 columns, in the same\n")
    o.write("order, that the champion's preprocessor expects. Order matters: the endpoint\n")
    o.write("receives a columns/data pair and a reordered list silently scores garbage\n")
    o.write("rather than erroring, so FEATURE_ORDER is the single source of truth and the\n")
    o.write("UI form is rendered from it rather than hand-written alongside it.\n\n")
    o.write('Regenerate with tools/gen_schema.py after any change to the feature set.\n"""\n\n')
    o.write("from __future__ import annotations\n\n")
    o.write("FEATURE_ORDER: list[str] = [\n")
    for c in cols:
        o.write(f'    "{c}",\n')
    o.write("]\n\n")
    o.write("# Fields the model treats as 0/1 flags; the UI renders them as toggles and the\n")
    o.write("# API validates them rather than passing an arbitrary float through.\n")
    o.write("BINARY_FEATURES: frozenset[str] = frozenset({\n")
    for b in sorted(BINARY):
        o.write(f'    "{b}",\n')
    o.write("})\n\n")
    o.write('# One real March 2025 block. Powers the "Load sample" button and the smoke test.\n')
    o.write("SAMPLE_ROW: dict[str, float] = {\n")
    for c in cols:
        o.write(f"    \"{c}\": {sample[c]!r},\n")
    o.write("}\n\n")
    o.write("# (group title, help text, [(field, label), ...]) — drives form rendering.\n")
    o.write("FEATURE_GROUPS: list[tuple[str, str, list[tuple[str, str]]]] = [\n")
    for title, help_, fs in GROUPS:
        o.write(f'    (\n        "{title}",\n        "{help_}",\n        [\n')
        for f in fs:
            o.write(f'            ("{f}", "{f.replace("_", " ")}"),\n')
        o.write("        ],\n    ),\n")
    o.write("]\n")

    DEST.write_text(o.getvalue(), encoding="utf-8")
    print(f"wrote {DEST.relative_to(ROOT)}: {len(cols)} features, {len(GROUPS)} groups")


if __name__ == "__main__":
    main()
