import json
import requests
import altair as alt
import pandas as pd
import streamlit as st
import snowflake.connector

# ── Configuration ────────────────────────────────────────────────────────────

SEMANTIC_VIEW = "SUPPLYLENS.ANALYTICS.SUPPLY_CHAIN_V2"
AGENT_NAME = "SUPPLYLENS.ANALYTICS.SUPPLY_CHAIN_AGENT"
WAREHOUSE = "SUPPLYLENS_PUBLIC_WH"

SUGGESTED_QUESTIONS_COMMERCIAL = [
    "What is our overall on-time delivery rate?",
    "What is our overall fill rate?",
    "What is the total revenue by customer market segment?",
    "What is the on-time delivery rate by ship mode?",
    "Which suppliers have the lowest on-time delivery rate?",
]

SUGGESTED_QUESTIONS_LOGISTICS = [
    "What is our logistics on-time shipment rate?",
    "What is the freight cost by carrier?",
    "Which plants are exceeding their daily capacity?",
    "What is the freight cost by transport mode?",
    "What is the ship-ahead rate?",
]

METRIC_DEFINITIONS = {
    "on_time_delivery_rate": {
        "name": "On-Time Delivery Rate",
        "formula": "COUNT(receipt_date \u2264 commit_date) / COUNT(*) \u00d7 100",
        "scope": "Fulfilled line items only (LINE_STATUS = 'F')",
        "notes": "Derived from LINEITEM: L_RECEIPTDATE vs L_COMMITDATE.",
    },
    "fill_rate": {
        "name": "Fill Rate",
        "formula": "SUM(non-returned qty) / SUM(total qty) \u00d7 100",
        "scope": "Fulfilled line items only (LINE_STATUS = 'F')",
        "notes": "Returned items identified by L_RETURNFLAG = 'R'.",
    },
    "days_of_inventory": {
        "name": "Days of Inventory",
        "formula": "AVG(available_qty) / (SUM(qty) / COUNT(DISTINCT ship_date))",
        "scope": "Per part-supplier combination",
        "notes": "Approximation. PARTSUPP.PS_AVAILQTY is a static snapshot, not time-series.",
    },
    "landed_cost": {
        "name": "Landed Cost",
        "formula": "SUM(supply_cost \u00d7 qty) + SUM(net_price \u00d7 tax_rate)",
        "scope": "Per line item, aggregatable to any level",
        "notes": "TPC-H has no separate freight cost; tax is the delivery-cost proxy.",
    },
    "revenue": {
        "name": "Total Revenue (Net)",
        "formula": "SUM(extended_price \u00d7 (1 \u2212 discount))",
        "scope": "All line items",
        "notes": "After discount, before tax.",
    },
    "return_rate": {
        "name": "Return Rate",
        "formula": "COUNT(returned) / COUNT(*) \u00d7 100",
        "scope": "Fulfilled line items only",
        "notes": "L_RETURNFLAG = 'R' indicates a return.",
    },
    # ── Logistics domain (Brunel) ──
    "on_time_shipment_rate": {
        "name": "On-Time Shipment Rate (Logistics)",
        "formula": "AVG(CASE WHEN SHIP_LATE_DAYS = 0 THEN 100 ELSE 0 END)",
        "scope": "All 9,215 Brunel logistics orders (100% coverage)",
        "notes": "Based on SHIP_LATE_DAYS = 0, not receipt vs commit date. Source: Brunel dataset.",
    },
    "freight_cost": {
        "name": "Freight Cost (Logistics)",
        "formula": "MAX(minimum_cost, weight_kg \u00d7 rate_per_kg)",
        "scope": "Per logistics order, where a carrier rate match exists",
        "notes": "75.87% coverage. NULL for CRF (customer-arranged) and weight-gap orders \u2014 never zero. Source: Brunel dataset.",
    },
    "warehouse_cost": {
        "name": "Warehouse Cost (Logistics)",
        "formula": "cost_per_unit \u00d7 unit_quantity",
        "scope": "All 9,215 Brunel logistics orders (100% coverage)",
        "notes": "Storage cost at the fulfilling plant. Source: Brunel dataset.",
    },
    "logistics_landed_cost": {
        "name": "Total Landed Cost (Logistics)",
        "formula": "warehouse_cost + freight_cost",
        "scope": "Per logistics order, where freight cost is available",
        "notes": "75.87% coverage. NULL when freight cost is unavailable \u2014 missing freight is NOT imputed as zero. Source: Brunel dataset.",
    },
    "ship_ahead_rate": {
        "name": "Ship-Ahead Rate (Logistics)",
        "formula": "AVG(CASE WHEN SHIP_AHEAD_DAYS > 0 THEN 100 ELSE 0 END)",
        "scope": "All 9,215 Brunel logistics orders (100% coverage)",
        "notes": "Percentage of orders shipped ahead of schedule. Source: Brunel dataset.",
    },
    "plant_utilization": {
        "name": "Plant Utilization (Logistics)",
        "formula": "daily_orders / daily_capacity \u00d7 100",
        "scope": "Per plant (19 plants in Brunel dataset)",
        "notes": "Values >100% indicate historical routing exceeded target capacity constraints. Source: Brunel dataset.",
    },
}

# Canonical SQL for each verified query, exactly as registered in the
# semantic view.  The Analyst API rewrites verified-query SQL to reference
# its own CTEs, so the fragment it returns in confidence.verified_query_used
# is not independently executable.  When the Analyst-generated SQL fails
# (e.g. CTE column-alias mismatches), we fall back to these originals.
VERIFIED_QUERY_SQL = {
    "OVERALL_ON_TIME_DELIVERY_RATE": """
        SELECT ROUND(COUNT_IF(L_RECEIPTDATE <= L_COMMITDATE) * 100.0 / COUNT(*), 2)
               AS on_time_delivery_rate_pct
          FROM SUPPLYLENS.RAW.LINEITEM
         WHERE L_LINESTATUS = 'F'
    """,
    "ON_TIME_DELIVERY_BY_SHIP_MODE": """
        SELECT L_SHIPMODE AS ship_mode,
               ROUND(COUNT_IF(L_RECEIPTDATE <= L_COMMITDATE) * 100.0 / COUNT(*), 2)
               AS on_time_delivery_rate_pct
          FROM SUPPLYLENS.RAW.LINEITEM
         WHERE L_LINESTATUS = 'F'
         GROUP BY L_SHIPMODE
         ORDER BY on_time_delivery_rate_pct DESC
    """,
    "WORST_SUPPLIERS_BY_OTD": """
        SELECT S.S_NAME AS supplier_name,
               ROUND(COUNT_IF(L.L_RECEIPTDATE <= L.L_COMMITDATE) * 100.0 / COUNT(*), 2)
               AS on_time_delivery_rate_pct,
               COUNT(*) AS fulfilled_line_items
          FROM SUPPLYLENS.RAW.LINEITEM L
          JOIN SUPPLYLENS.RAW.SUPPLIER S ON L.L_SUPPKEY = S.S_SUPPKEY
         WHERE L.L_LINESTATUS = 'F'
         GROUP BY S.S_NAME
         ORDER BY on_time_delivery_rate_pct ASC
         LIMIT 10
    """,
    "OVERALL_FILL_RATE": """
        SELECT ROUND(
                 SUM(CASE WHEN L_RETURNFLAG != 'R' THEN L_QUANTITY ELSE 0 END) * 100.0
                 / SUM(L_QUANTITY), 2
               ) AS fill_rate_pct
          FROM SUPPLYLENS.RAW.LINEITEM
         WHERE L_LINESTATUS = 'F'
    """,
    "FILL_RATE_BY_MARKET_SEGMENT": """
        SELECT C.C_MKTSEGMENT AS market_segment,
               ROUND(
                 SUM(CASE WHEN L.L_RETURNFLAG != 'R' THEN L.L_QUANTITY ELSE 0 END) * 100.0
                 / SUM(L.L_QUANTITY), 2
               ) AS fill_rate_pct
          FROM SUPPLYLENS.RAW.LINEITEM L
          JOIN SUPPLYLENS.RAW.ORDERS O ON L.L_ORDERKEY = O.O_ORDERKEY
          JOIN SUPPLYLENS.RAW.CUSTOMER C ON O.O_CUSTKEY = C.C_CUSTKEY
         WHERE L.L_LINESTATUS = 'F'
         GROUP BY C.C_MKTSEGMENT
         ORDER BY fill_rate_pct DESC
    """,
    "REVENUE_BY_MARKET_SEGMENT": """
        SELECT C.C_MKTSEGMENT AS market_segment,
               ROUND(SUM(L.L_EXTENDEDPRICE * (1 - L.L_DISCOUNT)), 2) AS total_revenue
          FROM SUPPLYLENS.RAW.LINEITEM L
          JOIN SUPPLYLENS.RAW.ORDERS O ON L.L_ORDERKEY = O.O_ORDERKEY
          JOIN SUPPLYLENS.RAW.CUSTOMER C ON O.O_CUSTKEY = C.C_CUSTKEY
         GROUP BY C.C_MKTSEGMENT
         ORDER BY total_revenue DESC
    """,
    "TOP_10_CUSTOMERS_BY_ORDER_VALUE": """
        SELECT C.C_NAME AS customer_name,
               ROUND(SUM(O.O_TOTALPRICE), 2) AS total_order_value,
               COUNT(DISTINCT O.O_ORDERKEY) AS order_count
          FROM SUPPLYLENS.RAW.ORDERS O
          JOIN SUPPLYLENS.RAW.CUSTOMER C ON O.O_CUSTKEY = C.C_CUSTKEY
         GROUP BY C.C_NAME
         ORDER BY total_order_value DESC
         LIMIT 10
    """,
    "RETURN_RATE_BY_PART_BRAND": """
        SELECT P.P_BRAND AS part_brand,
               ROUND(
                 SUM(CASE WHEN L.L_RETURNFLAG = 'R' THEN 1 ELSE 0 END) * 100.0
                 / COUNT(*), 2
               ) AS return_rate_pct
          FROM SUPPLYLENS.RAW.LINEITEM L
          JOIN SUPPLYLENS.RAW.PARTSUPP PS
               ON L.L_PARTKEY = PS.PS_PARTKEY AND L.L_SUPPKEY = PS.PS_SUPPKEY
          JOIN SUPPLYLENS.RAW.PART P ON PS.PS_PARTKEY = P.P_PARTKEY
         WHERE L.L_LINESTATUS = 'F'
         GROUP BY P.P_BRAND
         ORDER BY return_rate_pct DESC
    """,
    "ON_TIME_DELIVERY_TREND_BY_YEAR": """
        SELECT YEAR(L_SHIPDATE) AS ship_year,
               ROUND(COUNT_IF(L_RECEIPTDATE <= L_COMMITDATE) * 100.0 / COUNT(*), 2)
               AS on_time_delivery_rate_pct,
               COUNT(*) AS total_fulfilled_items
          FROM SUPPLYLENS.RAW.LINEITEM
         WHERE L_LINESTATUS = 'F'
         GROUP BY YEAR(L_SHIPDATE)
         ORDER BY ship_year
    """,
    "AVERAGE_DAYS_OF_INVENTORY_BY_SUPPLIER": """
        SELECT S.S_NAME AS supplier_name,
               ROUND(
                 AVG(PS.PS_AVAILQTY)
                 / NULLIF(SUM(L.L_QUANTITY) / NULLIF(COUNT(DISTINCT L.L_SHIPDATE), 0), 0),
                 1
               ) AS days_of_inventory
          FROM SUPPLYLENS.RAW.PARTSUPP PS
          JOIN SUPPLYLENS.RAW.SUPPLIER S ON PS.PS_SUPPKEY = S.S_SUPPKEY
          JOIN SUPPLYLENS.RAW.LINEITEM L
               ON PS.PS_PARTKEY = L.L_PARTKEY AND PS.PS_SUPPKEY = L.L_SUPPKEY
         GROUP BY S.S_NAME
         ORDER BY days_of_inventory DESC
         LIMIT 20
    """,
}


# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="SupplyLens",
    page_icon="\u2b21",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Metal Black Theme CSS ────────────────────────────────────────────────────

_CSS = """
<style>
/* ── Reset & Global ── */
:root {
    --bg-base: #080A0C;
    --bg-surface: #111417;
    --bg-elevated: #191D21;
    --border: #292E34;
    --border-hover: #3A4049;
    --text-primary: #F1F3F5;
    --text-secondary: #9AA3AD;
    --text-muted: #69727C;
    --accent: #5B8DEF;
    --accent-dim: rgba(91,141,239,0.12);
    --success: #3D9A6E;
    --success-dim: rgba(61,154,110,0.12);
    --error: #D45454;
    --error-dim: rgba(212,84,84,0.10);
    --radius: 8px;
    --radius-sm: 6px;
    --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Inter', sans-serif;
}
html, body, [data-testid="stAppViewContainer"] {
    background-color: var(--bg-base) !important;
    color: var(--text-primary) !important;
    font-family: var(--font) !important;
}
[data-testid="stAppViewBlockContainer"] { max-width: 1100px; margin: 0 auto; padding: 2rem 2rem; }
[data-testid="stHeader"] { background: var(--bg-base) !important; }
h1, h2, h3, h4, h5, h6 { color: var(--text-primary) !important; font-weight: 600 !important; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background-color: var(--bg-surface) !important;
    border-right: 1px solid var(--border) !important;
}
[data-testid="stSidebar"] * { color: var(--text-secondary) !important; }
[data-testid="stSidebar"] .sidebar-logo { color: var(--text-primary) !important; font-size: 20px; font-weight: 700; letter-spacing: -0.02em; margin-bottom: 2px; }
[data-testid="stSidebar"] .sidebar-subtitle { color: var(--text-muted) !important; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 24px; }
[data-testid="stSidebar"] .sidebar-section { color: var(--text-muted) !important; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 20px; margin-bottom: 8px; font-weight: 600; }
[data-testid="stSidebar"] .sidebar-item {
    display: block; padding: 6px 12px; border-radius: var(--radius-sm); font-size: 13px;
    color: var(--text-secondary) !important; margin-bottom: 2px; text-decoration: none;
}
[data-testid="stSidebar"] .sidebar-item.active {
    background: var(--bg-elevated); color: var(--text-primary) !important;
    border-left: 2px solid var(--accent); padding-left: 10px;
}
[data-testid="stSidebar"] .sidebar-status {
    margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--border);
    font-size: 11px; color: var(--text-muted) !important;
}
[data-testid="stSidebar"] .sidebar-status .status-dot {
    display: inline-block; width: 6px; height: 6px; border-radius: 50%;
    background: var(--success); margin-right: 6px; vertical-align: middle;
}

/* ── Eyebrow / Hero ── */
.eyebrow { font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-muted); margin-bottom: 8px; font-weight: 600; }
.hero-title { font-size: 28px; font-weight: 700; color: var(--text-primary); letter-spacing: -0.02em; margin-bottom: 6px; line-height: 1.2; }
.hero-sub { font-size: 14px; color: var(--text-secondary); margin-bottom: 32px; line-height: 1.5; }

/* ── Domain Badges ── */
.domain-badge {
    display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 10px;
    font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; vertical-align: middle;
}
.domain-badge.commercial { background: rgba(91,141,239,0.10); color: #7BA8F7; border: 1px solid rgba(91,141,239,0.2); }
.domain-badge.logistics { background: rgba(61,154,110,0.10); color: #5DBE8A; border: 1px solid rgba(61,154,110,0.2); }
.domain-badge.cross { background: rgba(255,255,255,0.05); color: var(--text-secondary); border: 1px solid var(--border); }

/* ── Suggested Question Cards ── */
.sq-section-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); font-weight: 600; margin-bottom: 10px; margin-top: 20px; }
.sq-grid { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }

/* Use Streamlit buttons for question cards */
[data-testid="stAppViewBlockContainer"] button[kind="secondary"] {
    background: var(--bg-surface) !important; border: 1px solid var(--border) !important;
    color: var(--text-secondary) !important; border-radius: var(--radius-sm) !important;
    font-size: 12.5px !important; padding: 8px 14px !important; font-weight: 400 !important;
    text-align: left !important; transition: border-color 0.15s, background 0.15s;
    white-space: normal !important; line-height: 1.4 !important;
}
[data-testid="stAppViewBlockContainer"] button[kind="secondary"]:hover {
    background: var(--bg-elevated) !important; border-color: var(--border-hover) !important;
    color: var(--text-primary) !important;
}

/* ── KPI Cards ── */
.kpi-card {
    background: var(--bg-surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 20px 16px; text-align: center;
}
.kpi-label {
    color: var(--text-muted); font-size: 10px; text-transform: uppercase;
    letter-spacing: 0.06em; margin-bottom: 8px; font-weight: 600;
}
.kpi-value { color: var(--text-primary); font-size: 36px; font-weight: 700; line-height: 1.1; letter-spacing: -0.02em; }
.kpi-support { color: var(--text-muted); font-size: 12px; margin-top: 6px; }

/* ── Comparison Cards (cross-domain) ── */
.comparison-row {
    display: flex; gap: 16px; margin: 16px 0;
}
.comparison-card {
    flex: 1; background: var(--bg-surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 24px 20px; text-align: center;
}
.comparison-card .comp-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); font-weight: 600; margin-bottom: 4px; }
.comparison-card .comp-value { font-size: 40px; font-weight: 700; color: var(--text-primary); line-height: 1.1; letter-spacing: -0.02em; margin: 8px 0; }
.comparison-card .comp-metric { font-size: 12px; color: var(--text-secondary); }
.comparison-vs { display: flex; align-items: center; justify-content: center; color: var(--text-muted); font-size: 12px; font-weight: 600; padding: 0 4px; letter-spacing: 0.04em; }

/* ── Source / Trust Badges ── */
.trust-bar {
    display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px; margin-bottom: 4px;
}
.trust-tag {
    display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px;
    border-radius: 4px; font-size: 10.5px; font-weight: 500;
    background: var(--bg-elevated); border: 1px solid var(--border); color: var(--text-muted);
}
.trust-tag.governed { background: var(--success-dim); border-color: rgba(61,154,110,0.25); color: var(--success); }
.trust-tag.verified { background: var(--success-dim); border-color: rgba(61,154,110,0.25); color: var(--success); }

/* ── Guardrail / Unavailable ── */
.guardrail-box {
    background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 24px; margin: 12px 0;
}
.guardrail-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); font-weight: 600; margin-bottom: 8px; }
.guardrail-title { font-size: 16px; font-weight: 600; color: var(--text-primary); margin-bottom: 12px; }
.guardrail-body { font-size: 13px; color: var(--text-secondary); line-height: 1.6; }

/* ── How It Works strip ── */
.how-strip {
    display: flex; align-items: center; justify-content: center; gap: 0;
    margin: 32px 0 24px 0; padding: 16px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
}
.how-step {
    text-align: center; padding: 0 20px; font-size: 11px; text-transform: uppercase;
    letter-spacing: 0.06em; color: var(--text-muted); font-weight: 600;
}
.how-step .step-label { color: var(--text-secondary); font-size: 12px; margin-top: 2px; text-transform: none; letter-spacing: 0; font-weight: 400; }
.how-arrow { color: var(--border-hover); font-size: 16px; padding: 0 4px; }

/* ── Data tables ── */
[data-testid="stDataFrame"] { border-radius: var(--radius) !important; overflow: hidden; }
[data-testid="stDataFrame"] table { background: var(--bg-surface) !important; }
[data-testid="stDataFrame"] th { background: var(--bg-elevated) !important; color: var(--text-secondary) !important; font-size: 11px !important; text-transform: uppercase; letter-spacing: 0.04em; }
[data-testid="stDataFrame"] td { color: var(--text-primary) !important; font-size: 13px !important; border-color: var(--border) !important; }

/* ── Expanders ── */
[data-testid="stExpander"] { border: 1px solid var(--border) !important; border-radius: var(--radius) !important; background: var(--bg-surface) !important; }
[data-testid="stExpander"] summary { color: var(--text-secondary) !important; font-size: 12px !important; }
[data-testid="stExpander"] summary:hover { color: var(--text-primary) !important; }

/* ── Chat messages ── */
[data-testid="stChatMessage"] { background: transparent !important; border: none !important; }

/* ── Code blocks ── */
pre, code { background: var(--bg-surface) !important; border: 1px solid var(--border) !important; border-radius: var(--radius-sm) !important; }

/* ── Alerts ── */
[data-testid="stAlert"] { background: var(--bg-surface) !important; border: 1px solid var(--border) !important; border-radius: var(--radius) !important; }

/* ── Divider ── */
hr { border-color: var(--border) !important; }

/* ── Metric Definition Panel ── */
.metric-def-panel { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 20px; margin: 8px 0; }
.metric-def-title { font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); font-weight: 600; margin-bottom: 12px; }
.metric-def-name { font-size: 14px; font-weight: 600; color: var(--text-primary); margin-bottom: 8px; }
.metric-def-row { display: flex; gap: 8px; margin-bottom: 4px; font-size: 12px; }
.metric-def-label { color: var(--text-muted); min-width: 60px; flex-shrink: 0; }
.metric-def-val { color: var(--text-secondary); }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg-base); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--border-hover); }

/* ── Problem / Approach section ── */
.context-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin: 24px 0 20px 0; }
@media (max-width: 768px) { .context-grid { grid-template-columns: 1fr; } }
.context-col { background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px 22px; }
.context-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); font-weight: 600; margin-bottom: 12px; }
.context-body { font-size: 13px; color: var(--text-secondary); line-height: 1.65; }
.context-body em { color: var(--text-muted); font-style: normal; font-size: 12px; }
.context-flow { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin-top: 12px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); font-weight: 600; }
.context-flow .cf-item { padding: 3px 8px; background: var(--bg-elevated); border: 1px solid var(--border); border-radius: 4px; color: var(--text-secondary); }
.context-flow .cf-arrow { color: var(--border-hover); }

.principles { display: flex; gap: 16px; margin: 16px 0 8px 0; }
@media (max-width: 768px) { .principles { flex-direction: column; } }
.principle { flex: 1; padding: 14px 16px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-sm); }
.principle .p-num { font-size: 10px; color: var(--text-muted); font-weight: 700; letter-spacing: 0.06em; margin-bottom: 4px; }
.principle .p-title { font-size: 12px; font-weight: 600; color: var(--text-primary); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
.principle .p-desc { font-size: 11.5px; color: var(--text-secondary); line-height: 1.5; }

.canonical-line { text-align: center; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); margin: 12px 0 4px 0; font-weight: 600; }
.canonical-metrics { text-align: center; font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; letter-spacing: 0.01em; }

.domain-chains { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 12px 0 4px 0; }
@media (max-width: 768px) { .domain-chains { grid-template-columns: 1fr; } }
.domain-chain { padding: 10px 14px; background: var(--bg-surface); border: 1px solid var(--border); border-radius: var(--radius-sm); }
.domain-chain .dc-label { margin-bottom: 6px; }
.domain-chain .dc-flow { font-size: 11px; color: var(--text-muted); line-height: 1.6; font-family: 'SF Mono', 'Fira Code', monospace; }
.domain-chain-note { text-align: center; font-size: 10px; color: var(--text-muted); margin: 8px 0 0 0; letter-spacing: 0.02em; }

.coco-provenance { text-align: center; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); margin-top: 8px; padding-top: 12px; border-top: 1px solid var(--border); font-weight: 500; }
.coco-provenance .coco-flow { color: var(--text-secondary); font-size: 10px; text-transform: none; letter-spacing: 0.02em; margin-top: 2px; font-weight: 400; }

/* ── Architecture panel (sidebar) ── */
.arch-panel { padding: 4px 0; }
.arch-domain-label { margin-bottom: 8px; margin-top: 4px; }
.arch-entity {
    display: flex; align-items: center; gap: 6px; padding: 4px 0; font-size: 11.5px;
}
.arch-name { color: var(--text-secondary) !important; font-weight: 500; min-width: 85px; flex-shrink: 0; }
.arch-arrow { color: var(--text-muted) !important; font-size: 10px; }
.arch-source { color: var(--text-muted) !important; font-size: 10px; font-family: 'SF Mono', 'Fira Code', monospace; word-break: break-all; }
.arch-separator { border-top: 1px solid var(--border); margin: 12px 0; }
.arch-note {
    font-size: 10px; color: var(--text-muted) !important; text-align: center;
    margin-top: 12px; padding-top: 8px; border-top: 1px solid var(--border);
    letter-spacing: 0.02em;
}

/* ── Chat composer (enlarged, full-width) ── */
[data-testid="stChatInput"] {
    max-width: 1100px !important; margin: 0 auto !important;
}
[data-testid="stChatInput"] textarea {
    background: var(--bg-surface) !important;
    border: 1px solid var(--border) !important;
    color: var(--text-primary) !important;
    border-radius: 10px !important;
    font-size: 15px !important;
    min-height: 64px !important;
    padding: 18px 52px 18px 20px !important;
    line-height: 1.5 !important;
    transition: border-color 0.15s !important;
}
[data-testid="stChatInput"] textarea:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 1px var(--accent) !important;
}
[data-testid="stChatInput"] textarea::placeholder {
    color: var(--text-muted) !important;
    font-size: 15px !important;
}
[data-testid="stChatInput"] button {
    background: var(--accent) !important;
    border: none !important;
    border-radius: 8px !important;
    width: 36px !important; height: 36px !important;
    min-width: 36px !important; min-height: 36px !important;
    padding: 0 !important;
    margin-right: 6px !important;
}
[data-testid="stChatInput"] button svg { fill: #fff !important; }
[data-testid="stChatInput"] button:hover { opacity: 0.9 !important; }

/* ── Sidebar expander override ── */
[data-testid="stSidebar"] [data-testid="stExpander"] {
    border: 1px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    background: var(--bg-elevated) !important;
    margin-bottom: 4px;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary {
    font-size: 12px !important; color: var(--text-secondary) !important; padding: 8px 12px !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {
    color: var(--text-primary) !important;
}
</style>
"""

st.markdown(_CSS, unsafe_allow_html=True)


# ── Connection ───────────────────────────────────────────────────────────────


def get_connection():
    if "conn" not in st.session_state or st.session_state.conn is None:
        try:
            cfg = st.secrets["snowflake"]
            conn_name = cfg.get("connection_name", "")

            if conn_name:
                st.session_state.conn = snowflake.connector.connect(
                    connection_name=conn_name,
                    warehouse=cfg.get("warehouse", WAREHOUSE),
                    database="SUPPLYLENS",
                    schema="ANALYTICS",
                )
            else:
                connect_args = dict(
                    account=cfg["account"],
                    user=cfg["user"],
                    warehouse=cfg.get("warehouse", WAREHOUSE),
                    role=cfg.get("role", "SUPPLYLENS_PUBLIC_RO"),
                    database="SUPPLYLENS",
                    schema="ANALYTICS",
                )
                auth = cfg.get("authenticator", "")
                if cfg.get("private_key", ""):
                    from cryptography.hazmat.primitives import serialization
                    pk = serialization.load_pem_private_key(
                        cfg["private_key"].encode(), password=None
                    )
                    connect_args["private_key"] = pk.private_bytes(
                        serialization.Encoding.DER,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    )
                elif auth:
                    connect_args["authenticator"] = auth
                    connect_args["client_store_temporary_credential"] = True
                else:
                    connect_args["password"] = cfg["password"]
                st.session_state.conn = snowflake.connector.connect(**connect_args)
        except Exception as e:
            st.error(f"Connection failed: {e}")
            st.stop()
    return st.session_state.conn


def get_host(conn):
    account = conn.account
    if "." in account:
        return f"{account}.snowflakecomputing.com"
    host = getattr(conn, "host", None)
    if host:
        return host
    return f"{account}.snowflakecomputing.com"


# ── Cortex Agent API ─────────────────────────────────────────────────────────


def call_agent(question: str) -> dict:
    conn = get_connection()
    host = get_host(conn)
    token = conn.rest.token

    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": question}]}
        ],
        "stream": False,
    }

    db, schema, name = AGENT_NAME.split(".")
    url = f"https://{host}/api/v2/databases/{db}/schemas/{schema}/agents/{name}:run"

    resp = requests.post(
        url=url,
        json=body,
        headers={
            "Authorization": f'Snowflake Token="{token}"',
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=180,
    )

    request_id = resp.headers.get("X-Snowflake-Request-Id", "unknown")

    if resp.status_code >= 400:
        raise Exception(
            f"Agent API error (request {request_id}): "
            f"{resp.status_code} \u2014 {resp.text[:500]}"
        )

    result = resp.json()
    result["request_id"] = request_id
    return result


def run_sql(sql: str) -> pd.DataFrame:
    conn = get_connection()
    try:
        return pd.read_sql(sql, conn)
    except Exception as e:
        raise Exception(f"SQL execution failed: {e}")


# ── Response parsing ─────────────────────────────────────────────────────────


def parse_response(response: dict) -> dict:
    """Extract text, SQL, result DataFrame, and suggestions from Agent response.

    When the Agent issues multiple SQL calls (e.g. one per domain for a
    cross-domain comparison), all successful result sets are collected.  If
    there is exactly one, it is returned directly.  If there are multiple with
    *different* column schemas, they are pivoted into a simple ``DOMAIN /
    METRIC / VALUE`` comparison table so that ``render_results()`` can display
    them as a table + chart without modification.
    """
    content = response.get("content", [])
    result = {
        "text": "",
        "sql": None,
        "suggestions": [],
        "confidence": None,
        "request_id": response.get("request_id", ""),
        "df": None,
    }

    sql_results: list[dict] = []

    for item in content:
        t = item.get("type")

        if t == "text":
            if result["text"]:
                result["text"] += "\n\n" + item.get("text", "")
            else:
                result["text"] = item.get("text", "")

        elif t == "suggested_queries":
            result["suggestions"] = [
                sq["query"] for sq in item.get("suggested_queries", [])
            ]

        elif t == "tool_result":
            tr = item.get("tool_result", {})
            if tr.get("status") != "success":
                continue
            if tr.get("type") != "system_execute_sql":
                continue

            for c in tr.get("content", []):
                if c.get("type") != "json":
                    continue
                payload = c.get("json", {})

                sql = payload.get("sql")
                verified = bool(payload.get("verified_query_used"))

                rs = payload.get("result_set")
                if not rs or not sql:
                    continue

                meta = rs.get("resultSetMetaData", {})
                col_names = [col["name"] for col in meta.get("rowType", [])]
                rows = rs.get("data", [])

                if not col_names or not rows:
                    continue

                df = pd.DataFrame(rows, columns=col_names)
                for col_meta in meta.get("rowType", []):
                    cname = col_meta["name"]
                    ctype = col_meta.get("type", "")
                    if ctype == "fixed":
                        scale = col_meta.get("scale", 0)
                        if scale and scale > 0:
                            df[cname] = pd.to_numeric(df[cname], errors="coerce")
                        else:
                            df[cname] = pd.to_numeric(df[cname], errors="coerce").astype("Int64")
                    elif ctype == "real":
                        df[cname] = pd.to_numeric(df[cname], errors="coerce")

                sql_results.append({"sql": sql, "df": df, "verified": verified})

    if len(sql_results) == 0:
        pass
    elif len(sql_results) == 1:
        result["sql"] = sql_results[0]["sql"]
        result["df"] = sql_results[0]["df"]
        if sql_results[0]["verified"]:
            result["confidence"] = {"verified_query_used": {"name": "VERIFIED"}}
    else:
        col_sets = [tuple(sr["df"].columns) for sr in sql_results]
        if len(set(col_sets)) == 1:
            result["df"] = pd.concat(
                [sr["df"] for sr in sql_results], ignore_index=True
            )
        else:
            comparison_rows = []
            for sr in sql_results:
                df = sr["df"]
                sql_text = sr["sql"]
                if "RAW_BRUNEL" in sql_text or "V_LOGISTICS" in sql_text or "V_PLANTS" in sql_text:
                    domain = "Logistics (Brunel)"
                else:
                    domain = "Commercial (TPC-H)"
                for col in df.columns:
                    val = df.iloc[0][col]
                    label = col.replace("_", " ").title()
                    comparison_rows.append({
                        "DOMAIN": domain,
                        "METRIC": label,
                        "VALUE": float(val) if pd.notna(val) else None,
                    })
            result["df"] = pd.DataFrame(comparison_rows)

        result["sql"] = "\n\n-- \u2500\u2500 Next domain \u2500\u2500\n\n".join(
            sr["sql"] for sr in sql_results
        )
        if any(sr["verified"] for sr in sql_results):
            result["confidence"] = {"verified_query_used": {"name": "VERIFIED"}}

    return result


# ── Metric detection ─────────────────────────────────────────────────────────


def detect_metrics(question: str) -> list[dict]:
    """Return metric definitions relevant to the question."""
    q = question.lower()
    matches = []
    keywords = {
        "on_time_delivery_rate": ["on-time", "on time", "otd", "delivery rate", "delivery performance"],
        "fill_rate": ["fill rate", "fulfillment"],
        "days_of_inventory": ["days of inventory", "doi", "inventory days", "days of supply"],
        "landed_cost": ["landed cost", "acquisition cost"],
        "revenue": ["revenue", "sales"],
        "return_rate": ["return rate", "return percentage"],
        "on_time_shipment_rate": ["shipment rate", "logistics otd", "logistics on-time", "ship late"],
        "freight_cost": ["freight cost", "freight spend", "shipping cost", "transport cost"],
        "warehouse_cost": ["warehouse cost", "storage cost", "wh cost"],
        "logistics_landed_cost": ["logistics landed", "total landed cost", "landed cost by plant"],
        "ship_ahead_rate": ["ship-ahead", "ship ahead", "ahead of schedule", "early shipment"],
        "plant_utilization": ["plant utilization", "plant capacity", "exceeding capacity", "exceeding their", "overloaded"],
    }
    for key, terms in keywords.items():
        if any(t in q for t in terms):
            matches.append(METRIC_DEFINITIONS[key])
    return matches


# ── Domain detection helper ──────────────────────────────────────────────────


def _detect_domain(parsed: dict) -> str:
    """Return 'commercial', 'logistics', 'cross', or 'unknown'."""
    sql = parsed.get("sql") or ""
    text = parsed.get("text") or ""
    has_brunel = "RAW_BRUNEL" in sql or "V_LOGISTICS" in sql or "V_PLANTS" in sql
    has_tpch = "RAW.LINEITEM" in sql or "RAW.ORDERS" in sql or "RAW.SUPPLIER" in sql or "RAW.CUSTOMER" in sql or "RAW.PART" in sql
    if has_brunel and has_tpch:
        return "cross"
    if has_brunel:
        return "logistics"
    if has_tpch:
        return "commercial"
    lower_text = text.lower()
    if "logistics" in lower_text and "commercial" in lower_text:
        return "cross"
    if "logistics" in lower_text or "brunel" in lower_text:
        return "logistics"
    return "commercial"


def _domain_badge_html(domain: str) -> str:
    if domain == "commercial":
        return '<span class="domain-badge commercial">Commercial \u00b7 TPC-H</span>'
    elif domain == "logistics":
        return '<span class="domain-badge logistics">Logistics \u00b7 Brunel</span>'
    elif domain == "cross":
        return '<span class="domain-badge commercial">Commercial \u00b7 TPC-H</span> <span class="domain-badge logistics">Logistics \u00b7 Brunel</span>'
    return ""


# ── Rendering ────────────────────────────────────────────────────────────────


def render_kpi_cards(df: pd.DataFrame):
    cols = st.columns(len(df.columns))
    for i, col_name in enumerate(df.columns):
        value = df.iloc[0][col_name]
        if isinstance(value, float):
            display = f"{value:,.2f}"
        elif isinstance(value, int):
            display = f"{value:,}"
        else:
            display = str(value)

        label = col_name.replace("_", " ").title()
        cols[i].markdown(
            f'<div class="kpi-card">'
            f'<div class="kpi-label">{label}</div>'
            f'<div class="kpi-value">{display}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )


def render_comparison(df: pd.DataFrame):
    """Render a cross-domain DOMAIN/METRIC/VALUE DataFrame as side-by-side cards."""
    if "DOMAIN" not in df.columns or "METRIC" not in df.columns or "VALUE" not in df.columns:
        render_table_and_chart(df)
        return

    domains = df["DOMAIN"].unique()
    if len(domains) != 2:
        render_table_and_chart(df)
        return

    # Find the primary rate metric for each domain
    def _pick_primary(domain_df):
        rate_rows = domain_df[domain_df["METRIC"].str.contains("Rate|Pct", case=False, na=False)]
        if not rate_rows.empty:
            return rate_rows.iloc[0]
        return domain_df.iloc[0]

    left_df = df[df["DOMAIN"] == domains[0]]
    right_df = df[df["DOMAIN"] == domains[1]]
    left = _pick_primary(left_df)
    right = _pick_primary(right_df)

    def _badge_class(d):
        return "commercial" if "Commercial" in d else "logistics"

    left_val = f"{left['VALUE']:,.2f}" if pd.notna(left["VALUE"]) else "N/A"
    right_val = f"{right['VALUE']:,.2f}" if pd.notna(right["VALUE"]) else "N/A"

    st.markdown(
        f'<div class="comparison-row">'
        f'<div class="comparison-card">'
        f'<div class="comp-label"><span class="domain-badge {_badge_class(left["DOMAIN"])}">{left["DOMAIN"]}</span></div>'
        f'<div class="comp-value">{left_val}%</div>'
        f'<div class="comp-metric">{left["METRIC"]}</div>'
        f'</div>'
        f'<div class="comparison-vs">VS</div>'
        f'<div class="comparison-card">'
        f'<div class="comp-label"><span class="domain-badge {_badge_class(right["DOMAIN"])}">{right["DOMAIN"]}</span></div>'
        f'<div class="comp-value">{right_val}%</div>'
        f'<div class="comp-metric">{right["METRIC"]}</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Show full comparison table below
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_table_and_chart(df: pd.DataFrame):
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    non_numeric_cols = [c for c in df.columns if c not in numeric_cols]

    _year_keywords = ["year", "yr", "month", "quarter", "period", "week"]
    promoted = []
    for col in list(numeric_cols):
        if any(kw in col.lower() for kw in _year_keywords):
            promoted.append(col)
            numeric_cols.remove(col)
    if promoted:
        for col in promoted:
            df[col] = df[col].astype(int).astype(str)
        non_numeric_cols = promoted + non_numeric_cols

    st.dataframe(df, use_container_width=True, hide_index=True)

    chart_df = df.copy()
    if not (len(non_numeric_cols) >= 1 and len(numeric_cols) >= 1):
        return
    if not (2 <= len(chart_df) <= 50):
        return

    x_col = non_numeric_cols[0]
    y_col = numeric_cols[0]

    import datetime as _dt
    _x_label_col = f"{x_col}__label"
    sample = chart_df[x_col].dropna().iloc[0] if not chart_df[x_col].dropna().empty else None
    if isinstance(sample, (_dt.date, _dt.datetime, pd.Timestamp)):
        vals = pd.to_datetime(chart_df[x_col], errors="coerce")
        all_jan_1 = vals.dropna().apply(lambda d: d.month == 1 and d.day == 1).all()
        if all_jan_1:
            chart_df[_x_label_col] = vals.dt.year.apply(lambda y: f"{y:04d}" if pd.notna(y) else "")
        else:
            chart_df[_x_label_col] = vals.dt.strftime("%Y-%m-%d")
        is_temporal = True
    else:
        is_temporal = x_col in promoted if promoted else False
        chart_df[_x_label_col] = chart_df[x_col].astype(str)

    x_field = _x_label_col
    x_sort = None if is_temporal else "-y"
    is_pct = any(kw in y_col.lower() for kw in ["rate", "pct", "percent", "ratio"])
    y_format = ".2f" if is_pct else ",.2f"
    y_title = y_col.replace("_", " ").title()
    if is_pct:
        y_title += " (%)"

    chart = (
        alt.Chart(chart_df)
        .mark_bar(color="#5B8DEF", cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X(f"{x_field}:N", sort=x_sort, title=x_col.replace("_", " ").title(),
                     axis=alt.Axis(labelColor="#9AA3AD", titleColor="#69727C", labelFontSize=11, titleFontSize=11, gridColor="#292E34")),
            y=alt.Y(f"{y_col}:Q", title=y_title, scale=alt.Scale(zero=True),
                     axis=alt.Axis(labelColor="#9AA3AD", titleColor="#69727C", labelFontSize=11, titleFontSize=11, gridColor="#1E2228")),
            tooltip=[
                alt.Tooltip(f"{x_field}:N", title=x_col.replace("_", " ").title()),
                alt.Tooltip(f"{y_col}:Q", title=y_title, format=y_format),
            ],
        )
        .configure_view(strokeWidth=0, fill="#0D1013")
        .configure(background="#0D1013")
        .properties(height=320)
    )

    st.altair_chart(chart, use_container_width=True)


def render_results(df: pd.DataFrame, domain: str = "unknown"):
    if df.empty:
        st.markdown('<p style="color: var(--text-muted); font-size: 13px;">Query returned no results.</p>', unsafe_allow_html=True)
        return

    # Cross-domain comparison
    if "DOMAIN" in df.columns and "METRIC" in df.columns and "VALUE" in df.columns:
        render_comparison(df)
        return

    # Single-row KPI
    if len(df) == 1:
        render_kpi_cards(df)
        return

    # Multi-row table + chart
    render_table_and_chart(df)


def render_metric_definitions(metrics: list[dict]):
    if not metrics:
        return
    with st.expander("Metric definitions", expanded=False):
        for m in metrics:
            st.markdown(
                f'<div class="metric-def-panel">'
                f'<div class="metric-def-title">Metric Definition</div>'
                f'<div class="metric-def-name">{m["name"]}</div>'
                f'<div class="metric-def-row"><span class="metric-def-label">Formula</span><span class="metric-def-val"><code>{m["formula"]}</code></span></div>'
                f'<div class="metric-def-row"><span class="metric-def-label">Scope</span><span class="metric-def-val">{m["scope"]}</span></div>'
                f'<div class="metric-def-row"><span class="metric-def-label">Notes</span><span class="metric-def-val">{m["notes"]}</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )


def _render_trust_bar(parsed: dict, domain: str):
    tags = []
    tags.append('<span class="trust-tag governed">\u2713 Governed</span>')
    tags.append(f'<span class="trust-tag">Supply Chain V2</span>')
    tags.append(f'<span class="trust-tag">Agent: Supply Chain Agent</span>')
    confidence = parsed.get("confidence", {})
    if confidence and confidence.get("verified_query_used"):
        tags.append('<span class="trust-tag verified">\u2713 Verified query</span>')
    badge = _domain_badge_html(domain)
    if badge:
        tags.append(badge)
    req = parsed.get("request_id", "N/A")[:12]
    tags.append(f'<span class="trust-tag">Request: {req}\u2026</span>')
    st.markdown(f'<div class="trust-bar">{"".join(tags)}</div>', unsafe_allow_html=True)


def _is_guardrail_response(parsed: dict) -> bool:
    """Detect domain-boundary refusals (text-only, no SQL, no data)."""
    text = (parsed.get("text") or "").lower()
    if parsed.get("sql") or parsed.get("df") is not None:
        return False
    guardrail_phrases = ["not available", "unavailable", "cannot be calculated", "can't answer", "not allowed",
                         "does not contain", "no pricing", "no return", "not something the logistics"]
    return any(p in text for p in guardrail_phrases)


# ── Sidebar ──────────────────────────────────────────────────────────────────


def _render_sidebar():
    with st.sidebar:
        st.markdown('<div class="sidebar-logo">SUPPLYLENS</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-subtitle">Supply Chain Intelligence</div>', unsafe_allow_html=True)

        st.markdown('<div class="sidebar-section">Workspace</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-item active">Ask Agent</div>', unsafe_allow_html=True)

        st.markdown('<div class="sidebar-section">Operations</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-item">Commercial</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-item">Logistics</div>', unsafe_allow_html=True)

        st.markdown('<div class="sidebar-section">Governance</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-item">Metrics</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-item">Semantic Model</div>', unsafe_allow_html=True)

        st.markdown('<div class="sidebar-section">Data Architecture</div>', unsafe_allow_html=True)
        with st.expander("Supply Chain Model", expanded=False):
            st.markdown(
                '<div class="arch-panel">'
                '<div class="arch-domain-label"><span class="domain-badge commercial">Commercial \u00b7 TPC-H</span></div>'
                '<div class="arch-entity"><span class="arch-name">Supplier</span><span class="arch-arrow">\u2192</span><span class="arch-source">RAW.SUPPLIER</span></div>'
                '<div class="arch-entity"><span class="arch-name">Part</span><span class="arch-arrow">\u2192</span><span class="arch-source">RAW.PART</span></div>'
                '<div class="arch-entity"><span class="arch-name">Part Supplier</span><span class="arch-arrow">\u2192</span><span class="arch-source">RAW.PARTSUPP</span></div>'
                '<div class="arch-entity"><span class="arch-name">Order</span><span class="arch-arrow">\u2192</span><span class="arch-source">RAW.ORDERS</span></div>'
                '<div class="arch-entity"><span class="arch-name">Line Item</span><span class="arch-arrow">\u2192</span><span class="arch-source">RAW.LINEITEM</span></div>'
                '<div class="arch-entity"><span class="arch-name">Customer</span><span class="arch-arrow">\u2192</span><span class="arch-source">RAW.CUSTOMER</span></div>'
                '<div class="arch-separator"></div>'
                '<div class="arch-domain-label"><span class="domain-badge logistics">Logistics \u00b7 Brunel</span></div>'
                '<div class="arch-entity"><span class="arch-name">Plant</span><span class="arch-arrow">\u2192</span><span class="arch-source">RAW_BRUNEL.V_PLANTS</span></div>'
                '<div class="arch-entity"><span class="arch-name">Product</span><span class="arch-arrow">\u2192</span><span class="arch-source">RAW_BRUNEL.PRODUCTS_PER_PLANT</span></div>'
                '<div class="arch-entity"><span class="arch-name">Shipment / Order</span><span class="arch-arrow">\u2192</span><span class="arch-source">RAW_BRUNEL.V_LOGISTICS_ORDERS</span></div>'
                '<div class="arch-entity"><span class="arch-name">Port</span><span class="arch-arrow">\u2192</span><span class="arch-source">RAW_BRUNEL.PLANT_PORTS</span></div>'
                '<div class="arch-note">Two governed domains \u00b7 independent source systems</div>'
                '</div>',
                unsafe_allow_html=True,
            )

        with st.expander("Architecture & Approach", expanded=False):
            st.markdown(
                '<div class="arch-panel">'
                '<div class="context-label">The Supply Chain Problem</div>'
                '<div style="font-size:12px;color:#9AA3AD;line-height:1.6;margin-bottom:12px;">'
                'Supply-chain data is scattered across operational systems with inconsistent '
                'definitions. The same question can produce different answers across teams. '
                'SupplyLens demonstrates this through two independent domains: '
                '<span class="domain-badge commercial">Commercial</span> + '
                '<span class="domain-badge logistics">Logistics</span>'
                '</div>'
                '<div class="context-label">The SupplyLens Approach</div>'
                '<div style="font-size:12px;color:#9AA3AD;line-height:1.6;margin-bottom:12px;">'
                'Build a governed ontology, express business meaning through a semantic model, '
                'and let Cortex Agent translate questions into governed analytics.'
                '</div>'
                '<div class="context-flow" style="margin-bottom:12px;">'
                '<span class="cf-item">Data</span><span class="cf-arrow">\u2192</span>'
                '<span class="cf-item">Entities</span><span class="cf-arrow">\u2192</span>'
                '<span class="cf-item">Metrics</span><span class="cf-arrow">\u2192</span>'
                '<span class="cf-item">Semantic Model</span><span class="cf-arrow">\u2192</span>'
                '<span class="cf-item">Agent</span><span class="cf-arrow">\u2192</span>'
                '<span class="cf-item">Answer</span>'
                '</div>'
                '<div class="arch-separator"></div>'
                '<div class="context-label">Core Principles</div>'
                '<div style="font-size:11px;color:#9AA3AD;line-height:1.8;">'
                '<strong style="color:#F1F3F5;">01 Model</strong> \u2014 Business entities, relationships &amp; domains<br>'
                '<strong style="color:#F1F3F5;">02 Govern</strong> \u2014 Canonical metrics &amp; semantic definitions<br>'
                '<strong style="color:#F1F3F5;">03 Answer</strong> \u2014 Natural-language analytics with guardrails'
                '</div>'
                '<div class="arch-separator"></div>'
                '<div class="context-label">Canonical Metrics</div>'
                '<div style="font-size:11px;color:#69727C;line-height:1.8;">'
                '<span class="domain-badge commercial" style="margin-bottom:4px;">Commercial</span> '
                'OTD \u00b7 Fill Rate \u00b7 Revenue \u00b7 Days of Inventory \u00b7 Return Rate<br>'
                '<span class="domain-badge logistics" style="margin-top:4px;">Logistics</span> '
                'Shipment OTD \u00b7 Freight Cost \u00b7 Warehouse Cost \u00b7 Landed Cost \u00b7 Ship-Ahead \u00b7 Utilization'
                '</div>'
                '<div class="arch-separator"></div>'
                '<div class="arch-note">Built &amp; validated with Snowflake CoCo<br>'
                '<span style="font-size:9px;">Planning \u2192 Development \u2192 Execution \u2192 Testing</span></div>'
                '</div>',
                unsafe_allow_html=True,
            )

        st.markdown('<div class="sidebar-section">System</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-item">Agent</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-item">Connection</div>', unsafe_allow_html=True)

        st.markdown(
            '<div class="sidebar-status">'
            '<span class="status-dot"></span> Connected<br>'
            '<span style="color:#69727C;font-size:10px;">Cortex Agent &middot; Supply Chain V2</span>'
            '</div>',
            unsafe_allow_html=True,
        )


# ── Main app ─────────────────────────────────────────────────────────────────


def main():
    _render_sidebar()

    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # ── Hero / Empty state ──
    if not st.session_state.messages:
        st.markdown('<div class="eyebrow">Supply Chain Intelligence</div>', unsafe_allow_html=True)
        st.markdown('<div class="hero-title">Ask your supply chain.</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="hero-sub">'
            'Governed analytics across commercial and logistics operations. '
            'Powered by Cortex Agent &middot; <code>SUPPLY_CHAIN_V2</code>'
            '</div>',
            unsafe_allow_html=True,
        )

        # Suggested questions
        st.markdown('<div class="sq-section-label">Commercial \u00b7 TPC-H</div>', unsafe_allow_html=True)
        cols = st.columns(len(SUGGESTED_QUESTIONS_COMMERCIAL))
        for i, q in enumerate(SUGGESTED_QUESTIONS_COMMERCIAL):
            if cols[i].button(q, key=f"suggest_c_{i}", use_container_width=True):
                st.session_state.pending_question = q

        st.markdown('<div class="sq-section-label">Logistics \u00b7 Brunel</div>', unsafe_allow_html=True)
        cols = st.columns(len(SUGGESTED_QUESTIONS_LOGISTICS))
        for i, q in enumerate(SUGGESTED_QUESTIONS_LOGISTICS):
            if cols[i].button(q, key=f"suggest_l_{i}", use_container_width=True):
                st.session_state.pending_question = q

        # Subtle architecture line
        st.markdown(
            '<div style="text-align:center;margin:24px 0 8px 0;font-size:10px;color:#69727C;letter-spacing:0.04em;">'
            'ASK \u2192 CORTEX AGENT \u2192 SEMANTIC MODEL \u2192 GOVERNED ANSWER'
            ' &nbsp;&middot;&nbsp; Built with Snowflake CoCo'
            '</div>',
            unsafe_allow_html=True,
        )

    # ── Chat history ──
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                _render_assistant_message(msg)

    # Chat input
    user_input = st.chat_input("Ask a supply-chain question\u2026")

    # Handle pending suggestion or new input
    question = None
    if hasattr(st.session_state, "pending_question") and st.session_state.pending_question:
        question = st.session_state.pending_question
        st.session_state.pending_question = None
    elif user_input:
        question = user_input

    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("Querying SupplyLens\u2026"):
                try:
                    response = call_agent(question)
                    parsed = parse_response(response)
                    domain = _detect_domain(parsed)

                    assistant_msg = {
                        "role": "assistant",
                        "parsed": parsed,
                        "question": question,
                        "df": None,
                        "error": None,
                        "domain": domain,
                    }

                    # Guardrail response
                    if _is_guardrail_response(parsed):
                        st.markdown(
                            f'<div class="guardrail-box">'
                            f'<div class="guardrail-label">Metric Unavailable</div>'
                            f'<div class="guardrail-body">{parsed["text"]}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        _render_trust_bar(parsed, domain)
                        st.session_state.messages.append(assistant_msg)
                    else:
                        # Domain badge
                        st.markdown(_domain_badge_html(domain), unsafe_allow_html=True)

                        if parsed["text"]:
                            st.markdown(parsed["text"])

                        df = parsed.get("df")
                        if df is not None and not df.empty:
                            assistant_msg["df"] = df
                            render_results(df, domain)
                        elif parsed["sql"]:
                            try:
                                df = run_sql(parsed["sql"])
                                assistant_msg["df"] = df
                                render_results(df, domain)
                            except Exception as sql_err:
                                vq_name = (
                                    parsed.get("confidence", {})
                                    .get("verified_query_used", {})
                                    .get("name", "")
                                )
                                fallback_sql = VERIFIED_QUERY_SQL.get(vq_name)
                                if fallback_sql:
                                    try:
                                        df = run_sql(fallback_sql)
                                        assistant_msg["df"] = df
                                        parsed["sql"] = fallback_sql
                                        render_results(df, domain)
                                    except Exception as e2:
                                        assistant_msg["error"] = str(e2)
                                        st.error(f"SQL execution error: {e2}")
                                else:
                                    assistant_msg["error"] = str(sql_err)
                                    st.error(f"SQL execution error: {sql_err}")

                        # Trust bar
                        _render_trust_bar(parsed, domain)

                        if parsed["sql"]:
                            with st.expander("View generated SQL", expanded=False):
                                st.code(parsed["sql"], language="sql")

                        if parsed["suggestions"] and not parsed["sql"] and not parsed["text"]:
                            st.markdown("**Suggested questions:**")
                            for s in parsed["suggestions"]:
                                st.markdown(f"- {s}")

                        metrics = detect_metrics(question)
                        render_metric_definitions(metrics)

                        st.session_state.messages.append(assistant_msg)

                except Exception as e:
                    error_msg = {
                        "role": "assistant",
                        "parsed": None,
                        "question": question,
                        "df": None,
                        "error": str(e),
                        "domain": "unknown",
                    }
                    st.error(f"Error: {e}")
                    st.session_state.messages.append(error_msg)

        st.rerun()


def _render_assistant_message(msg: dict):
    """Re-render a stored assistant message from history."""
    parsed = msg.get("parsed") or {}
    domain = msg.get("domain", "unknown")

    if not parsed and not msg.get("error"):
        return

    # Guardrail replay
    if _is_guardrail_response(parsed):
        st.markdown(
            f'<div class="guardrail-box">'
            f'<div class="guardrail-label">Metric Unavailable</div>'
            f'<div class="guardrail-body">{parsed.get("text", "")}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        _render_trust_bar(parsed, domain)
        return

    st.markdown(_domain_badge_html(domain), unsafe_allow_html=True)

    if parsed.get("text"):
        st.markdown(parsed["text"])

    df = msg.get("df")
    if df is not None and not df.empty:
        render_results(df, domain)
    elif msg.get("error"):
        st.error(f"SQL execution error: {msg['error']}")

    _render_trust_bar(parsed, domain)

    if parsed.get("sql"):
        with st.expander("View generated SQL", expanded=False):
            st.code(parsed["sql"], language="sql")

    if parsed.get("suggestions") and not parsed.get("sql") and not parsed.get("text"):
        st.markdown("**Suggested questions:**")
        for s in parsed["suggestions"]:
            st.markdown(f"- {s}")

    if msg.get("question"):
        metrics = detect_metrics(msg["question"])
        render_metric_definitions(metrics)


if __name__ == "__main__":
    main()
