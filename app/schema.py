"""
schema.py
=========
The model's input contract, in one place.

Generated from scoring/sample_request.json — the same 37 columns, in the same
order, that the champion's preprocessor expects. Order matters: the endpoint
receives a columns/data pair and a reordered list silently scores garbage
rather than erroring, so FEATURE_ORDER is the single source of truth and the
UI form is rendered from it rather than hand-written alongside it.

Regenerate with tools/gen_schema.py after any change to the feature set.
"""

from __future__ import annotations

FEATURE_ORDER: list[str] = [
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "block_sin",
    "block_cos",
    "day_of_month",
    "is_weekend",
    "is_peak_hour",
    "is_morning_ramp",
    "is_off_peak",
    "dam_purchase_bid",
    "dam_sell_bid",
    "dam_mcv",
    "dam_volume",
    "dam_bid_imbalance",
    "rtm_purchase_bid",
    "rtm_sell_bid",
    "rtm_mcv",
    "rtm_volume",
    "mcp_spread",
    "dam_mcp_lag_1d",
    "dam_mcp_lag_2d",
    "dam_mcp_lag_7d",
    "dam_mcp_roll_4h",
    "dam_mcp_roll_24h",
    "dam_mcp_roll_std_24h",
    "avg_temp",
    "avg_humidity",
    "avg_windspeed",
    "avg_cloud_cover",
    "total_rainfall",
    "is_ipl_match",
    "is_event",
    "is_festival",
    "is_wedding_season",
    "impact",
]

# Fields the model treats as 0/1 flags; the UI renders them as toggles and the
# API validates them rather than passing an arbitrary float through.
BINARY_FEATURES: frozenset[str] = frozenset({
    "is_event",
    "is_festival",
    "is_ipl_match",
    "is_morning_ramp",
    "is_off_peak",
    "is_peak_hour",
    "is_wedding_season",
    "is_weekend",
})

# One real March 2025 block. Powers the "Load sample" button and the smoke test.
SAMPLE_ROW: dict[str, float] = {
    "hour_sin": 0.0,
    "hour_cos": 1.0,
    "dow_sin": -0.9749279121818236,
    "dow_cos": -0.2225209339563146,
    "block_sin": 0.0,
    "block_cos": 1.0,
    "day_of_month": 8.0,
    "is_weekend": 1.0,
    "is_peak_hour": 0.0,
    "is_morning_ramp": 0.0,
    "is_off_peak": 1.0,
    "dam_purchase_bid": 12472.3,
    "dam_sell_bid": 10980.6,
    "dam_mcv": 6350.3,
    "dam_volume": 6350.3,
    "dam_bid_imbalance": 0.06360407454670403,
    "rtm_purchase_bid": 5588.8,
    "rtm_sell_bid": 5574.2,
    "rtm_mcv": 3276.705,
    "rtm_volume": 3276.705,
    "mcp_spread": 542.8599999999997,
    "dam_mcp_lag_1d": 2999.58,
    "dam_mcp_lag_2d": 4000.74,
    "dam_mcp_lag_7d": 3072.62,
    "dam_mcp_roll_4h": 4875.3653125,
    "dam_mcp_roll_24h": 5270.99421875,
    "dam_mcp_roll_std_24h": 2771.3125654325945,
    "avg_temp": 20.0,
    "avg_humidity": 53.25,
    "avg_windspeed": 12.1203125,
    "avg_cloud_cover": 5.71875,
    "total_rainfall": 1.8,
    "is_ipl_match": 0.0,
    "is_event": 0.0,
    "is_festival": 0.0,
    "is_wedding_season": 0.0,
    "impact": 1.0,
}

# (group title, help text, [(field, label), ...]) — drives form rendering.
FEATURE_GROUPS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "Time & cycle",
        "Cyclical encodings and calendar position of the delivery block.",
        [
            ("hour_sin", "hour sin"),
            ("hour_cos", "hour cos"),
            ("dow_sin", "dow sin"),
            ("dow_cos", "dow cos"),
            ("block_sin", "block sin"),
            ("block_cos", "block cos"),
            ("day_of_month", "day of month"),
            ("is_weekend", "is weekend"),
            ("is_peak_hour", "is peak hour"),
            ("is_morning_ramp", "is morning ramp"),
            ("is_off_peak", "is off peak"),
        ],
    ),
    (
        "Day-ahead market",
        "DAM bid/clearing volumes for the block.",
        [
            ("dam_purchase_bid", "dam purchase bid"),
            ("dam_sell_bid", "dam sell bid"),
            ("dam_mcv", "dam mcv"),
            ("dam_volume", "dam volume"),
            ("dam_bid_imbalance", "dam bid imbalance"),
        ],
    ),
    (
        "Real-time market",
        "RTM counterparts plus the DAM/RTM clearing-price spread.",
        [
            ("rtm_purchase_bid", "rtm purchase bid"),
            ("rtm_sell_bid", "rtm sell bid"),
            ("rtm_mcv", "rtm mcv"),
            ("rtm_volume", "rtm volume"),
            ("mcp_spread", "mcp spread"),
        ],
    ),
    (
        "Price history",
        "Lagged and rolling DAM MCP. These dominate feature importance.",
        [
            ("dam_mcp_lag_1d", "dam mcp lag 1d"),
            ("dam_mcp_lag_2d", "dam mcp lag 2d"),
            ("dam_mcp_lag_7d", "dam mcp lag 7d"),
            ("dam_mcp_roll_4h", "dam mcp roll 4h"),
            ("dam_mcp_roll_24h", "dam mcp roll 24h"),
            ("dam_mcp_roll_std_24h", "dam mcp roll std 24h"),
        ],
    ),
    (
        "Weather",
        "Load-weighted averages across the 66 station network.",
        [
            ("avg_temp", "avg temp"),
            ("avg_humidity", "avg humidity"),
            ("avg_windspeed", "avg windspeed"),
            ("avg_cloud_cover", "avg cloud cover"),
            ("total_rainfall", "total rainfall"),
        ],
    ),
    (
        "Demand events",
        "Calendar drivers of anomalous demand.",
        [
            ("is_ipl_match", "is ipl match"),
            ("is_event", "is event"),
            ("is_festival", "is festival"),
            ("is_wedding_season", "is wedding season"),
            ("impact", "impact"),
        ],
    ),
]
