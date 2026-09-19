# SupplyLens Architecture

## 1. Problem

Supply-chain data is distributed across operational domains and systems with inconsistent business definitions. Different teams using different data sources, column names, and calculation methods can receive different answers to the same business question.

SupplyLens addresses this through a governed pipeline:

```
Source Data → Business Ontology → Governed Metrics → Semantic Model → Cortex Agent → Trusted Answer
```

The implementation demonstrates this pattern using two independent, real-world datasets that share no common keys. This is intentional: it models the real-world scenario where commercial order data and logistics operational data originate from separate systems.

## 2. Solution

SupplyLens provides:

- **Business entities and relationships** modeled as a supply-chain ontology spanning two independent data domains.
- **Governed metric definitions** with explicit formulas, scopes, and coverage documentation.
- **A semantic view** (`SUPPLY_CHAIN_V2`) that expresses business meaning over raw tables and enforces consistent metric calculations.
- **A Cortex Agent** (`SUPPLY_CHAIN_AGENT`) that translates natural-language questions into governed SQL, routes questions to the correct domain, and executes queries server-side.
- **Domain-boundary guardrails** that refuse to fabricate unsupported metrics rather than returning incorrect answers.
- **Cross-domain analytics** that query independent domains separately and present results together without inventing joins.
- **Transparent traceability** showing the generated SQL, semantic view source, and governance metadata for every answer.

## 3. Architecture

```
Source Data (TPC-H + Brunel)
         |
   Validated Data Layer
   (RAW + RAW_BRUNEL schemas)
         |
   Business Entities & Relationships
   (12 logical tables, 11 relationships)
         |
   SUPPLY_CHAIN_V2
   (Governed semantic view with 16 metrics, 18 verified queries)
         |
   SUPPLY_CHAIN_AGENT (VERSION$2)
   (Cortex Agent with two-domain routing instructions)
         |
   Natural Language Question
         |
   Governed SQL / Server-side Execution
         |
   Validated Result
         |
   Streamlit Application
   (Metal Black enterprise UI)
```

### Data Domains

| Domain | Source | Schema | Description |
|---|---|---|---|
| **Commercial** | TPC-H benchmark | `SUPPLYLENS.RAW` | Orders, customers, suppliers, parts, pricing, delivery performance |
| **Logistics** | Brunel University dataset | `SUPPLYLENS.RAW_BRUNEL` | Plants, carriers, freight rates, warehouse costs, shipping timeliness |

These domains are **intentionally independent**. There are no fabricated cross-domain relationships. Brunel customers, products, and orders are completely separate entities from TPC-H customers, parts, and orders.

## 4. Commercial Ontology (TPC-H)

```
Region → Nation → Supplier → PartSupplier ← Part
                      |
         Nation → Customer → Order → LineItem → PartSupplier
```

| Entity | Source Table | Primary Key | Description |
|---|---|---|---|
| Region | `RAW.REGION` | R_REGIONKEY | Geographic regions |
| Nation | `RAW.NATION` | N_NATIONKEY | Nations within regions |
| Supplier | `RAW.SUPPLIER` | S_SUPPKEY | Parts suppliers |
| Part | `RAW.PART` | P_PARTKEY | Product catalog |
| PartSupplier | `RAW.PARTSUPP` | (PS_PARTKEY, PS_SUPPKEY) | Supplier-part cost and availability |
| Order | `RAW.ORDERS` | O_ORDERKEY | Customer purchase orders |
| LineItem | `RAW.LINEITEM` | (L_ORDERKEY, L_LINENUMBER) | Order line items with pricing and delivery |
| Customer | `RAW.CUSTOMER` | C_CUSTKEY | Order customers |

**Scale:** 150K customers, 1.5M orders, 6M line items, 10K suppliers, 200K parts.

## 5. Logistics Ontology (Brunel)

```
Plant → Product (eligibility)
Plant → Port (shipping links)
Plant → LogisticsOrder → Carrier → FreightRate
```

| Entity | Source Table/View | Primary Key | Description |
|---|---|---|---|
| Plant | `RAW_BRUNEL.V_PLANTS` | PLANT_CODE | 19 plants with cost/unit and daily capacity |
| Product | `RAW_BRUNEL.PRODUCTS_PER_PLANT` | (PLANT_CODE, PRODUCT_ID) | Plant-product fulfillment eligibility |
| Logistics Order | `RAW_BRUNEL.V_LOGISTICS_ORDERS` | ORDER_ID | 9,215 enriched orders with warehouse cost, freight cost, and timeliness |
| Port | `RAW_BRUNEL.PLANT_PORTS` | (PLANT_CODE, PORT) | Allowed plant-to-port shipping links |

**Staging views:**
- `V_LOGISTICS_ORDERS` enriches each order with warehouse cost (100% coverage), freight cost (75.87% coverage via carrier rate matching), and on-time status.
- `V_PLANTS` combines warehouse cost, capacity, and utilization into a single plant dimension.

**Domain independence:** The commercial and logistics domains share NO join keys. Brunel suppliers, customers, and products cannot be joined to TPC-H suppliers, customers, or parts.

## 6. Semantic Model

`SUPPLYLENS.ANALYTICS.SUPPLY_CHAIN_V2` is a Snowflake semantic view that provides governed business definitions over the raw data.

| Component | Count |
|---|---|
| Logical tables | 12 (8 commercial + 4 logistics) |
| Relationships | 11 (8 commercial + 3 logistics, zero cross-domain) |
| Dimensions | 36 (24 commercial + 12 logistics) |
| Facts | 25 (15 commercial + 10 logistics) |
| Metrics | 16 (11 commercial + 5 logistics) |
| Derived metrics | 2 (LANDED_COST, DAYS_OF_INVENTORY) |
| Verified queries | 18 (10 commercial + 8 logistics) |
| AI instructions | Domain routing, boundary rules, metric rules |

The semantic view includes `AI_SQL_GENERATION` instructions with explicit domain-boundary rules that prevent the Agent from substituting metrics across domains.

## 7. Metrics

### Commercial Domain (TPC-H)

| Metric | Formula | Scope |
|---|---|---|
| On-Time Delivery Rate | AVG(CASE WHEN fulfilled AND receipt <= commit THEN 100 ELSE 0 END) | Fulfilled line items |
| Fill Rate | non-returned qty / total qty * 100 | Fulfilled line items |
| Total Revenue | SUM(extended_price * (1 - discount)) | All line items |
| Return Rate | returned count / fulfilled count * 100 | Fulfilled line items |
| Days of Inventory | AVG(available_qty) / daily demand | Per part-supplier |
| Order Count | COUNT(orders) | All orders |
| Average Order Value | AVG(order total price) | All orders |
| Landed Cost | SUM(supply_cost * qty) + SUM(net_price * tax) | Per line item |

### Logistics Domain (Brunel)

| Metric | Formula | Coverage |
|---|---|---|
| On-Time Shipment Rate | AVG(CASE WHEN SHIP_LATE_DAYS = 0 THEN 100 ELSE 0 END) | 100% (all 9,215 orders) |
| Total Freight Cost | SUM(FREIGHT_COST) | 75.87% (NULL for CRF and weight-gap) |
| Total Warehouse Cost | SUM(cost_per_unit * unit_quantity) | 100% |
| Logistics Landed Cost | warehouse_cost + freight_cost | 75.87% (NULL when freight unavailable) |
| Ship-Ahead Rate | AVG(CASE WHEN SHIP_AHEAD_DAYS > 0 THEN 100 ELSE 0 END) | 100% |

**Plant Utilization** (daily_orders / daily_capacity * 100) is available as a verified query. Values above 100% indicate historical routing exceeded target capacity constraints.

Metrics are **domain-scoped**. Revenue, fill rate, and return rate exist only in the commercial domain. Freight cost, warehouse cost, and ship-ahead rate exist only in the logistics domain.

## 8. Governance

### Domain-Boundary Behavior

The semantic view and Agent instructions enforce domain boundaries:

| Question | Domain | Result |
|---|---|---|
| "What is our overall on-time delivery rate?" | Commercial | 37.83% |
| "What is our overall fill rate?" | Commercial | 50.66% |
| "What is our logistics on-time shipment rate?" | Logistics | 97.92% |
| "What is the revenue for logistics?" | **Unsupported** | Refused with explanation |
| "What is the fill rate for logistics?" | **Unsupported** | Refused with explanation |

When the Agent cannot compute a requested metric for a given domain, it explains why the metric is unavailable and what data would be required. It does not substitute a different metric or fabricate a result.

**This is a core design principle:** a governed refusal is preferable to an incorrect or misleading answer.

## 9. Cross-Domain Analytics

SupplyLens can answer questions that span both domains by querying each domain independently and presenting the results together.

**Example:** "Compare commercial on-time delivery with logistics on-time shipment rate."

| Domain | Metric | Value |
|---|---|---|
| Commercial (TPC-H) | On-Time Delivery Rate | 37.83% |
| Logistics (Brunel) | On-Time Shipment Rate | 97.92% |

The Agent issues separate SQL queries against each domain's tables, and the application parser combines the results into a comparison view. No cross-domain join is fabricated. The Agent's narrative explains that the metrics use different definitions (receipt vs commit date for commercial; ship-late-days for logistics).

## 10. Data Limitations

| Limitation | Detail |
|---|---|
| Single-date logistics snapshot | All Brunel orders are dated 2013-05-26 |
| Freight cost coverage | 75.87% of non-CRF orders have matched freight rates |
| Missing freight is NULL | Never imputed as zero |
| CRF orders | Customer-arranged freight has no carrier rate |
| Weight-gap orders | Some orders fall between carrier rate-band boundaries |
| No logistics revenue | Brunel contains no pricing or sales data |
| No logistics fill rate | Brunel contains no return/fulfillment status |
| No logistics supplier | Brunel does not model upstream suppliers |
| No cross-domain keys | TPC-H and Brunel share no common identifiers |
| Plant utilization >100% | Historical routing exceeded target capacity constraints |
| Days of inventory approximation | TPC-H PS_AVAILQTY is a static snapshot, not a time-series ledger |

## 11. Streamlit Application

The application (`app.py`, 1,312 lines) provides:

- **Metal Black enterprise UI** with obsidian/graphite theme
- **Natural-language query interface** via Cortex Agent REST API (non-streaming)
- **Suggested questions** organized by domain (5 commercial + 5 logistics)
- **KPI cards** for single-value results
- **Data tables** with themed styling
- **Altair bar charts** matching the Metal Black theme
- **Cross-domain comparison cards** (side-by-side VS layout)
- **Guardrail response boxes** for unsupported metrics
- **Governance/trust badges** (Governed, Verified, Domain, Agent, Semantic View)
- **Domain badges** (Commercial/TPC-H, Logistics/Brunel)
- **Generated SQL** visibility via collapsible expander
- **Metric definitions** panel with formula, scope, and notes
- **Sidebar** with navigation, Supply Chain Model mapping, Architecture & Approach panel, and connection status
- **Multi-result parser** that handles Agent responses with multiple SQL executions (cross-domain)

## 12. Security / Authentication

The application authenticates to Snowflake using a named connection from `~/.snowflake/connections.toml`, configured in `.streamlit/secrets.toml`. This inherits whatever authenticator the Snowflake CLI uses (currently OAuth via `OAUTH_AUTHORIZATION_CODE`).

The Cortex Agent REST API is called with a `Snowflake Token` header using the session token from the connector. All SQL execution happens server-side within the Agent; the application does not execute arbitrary user-provided SQL.

## 13. Sources & References

| Source | URL |
|---|---|
| Brunel Supply Chain Logistics Problem Dataset | https://brunel.figshare.com/articles/dataset/Supply_Chain_Logistics_Problem_Dataset/7558679 |
| Snowflake Cortex Code CLI | https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-cli |
| Snowflake Semantic Views | https://docs.snowflake.com/en/user-guide/views-semantic/overview |
| Snowflake Cortex Agents | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents |
| Hackathon | https://hack2skill.com/event/cococlihack-gccedition/ |
