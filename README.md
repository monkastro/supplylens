# SupplyLens

Governed conversational analytics for supply-chain data, built for the [Snowflake CoCo CLI Hackathon (GCC Edition)](https://hack2skill.com/event/cococlihack-gccedition/).

## Problem

Supply-chain data is scattered across ERP, logistics, supplier, and IoT systems with inconsistent definitions. The same question — "What is our on-time delivery rate?" — yields different answers across teams because each team uses a different data source, a different formula, and a different filter.

## Solution

SupplyLens builds a **governed supply-chain ontology** expressed as a Snowflake Semantic View. Business entities, relationships, and metric definitions are modeled once, so a natural-language layer (Cortex Agent) returns consistent, trustworthy answers. When a metric is unavailable for a given data domain, the system refuses the question rather than returning an incorrect answer.

**Two independent data domains, one governed model:**
- **Commercial (TPC-H):** On-Time Delivery Rate, Fill Rate, Revenue, Return Rate, Days of Inventory, Landed Cost
- **Logistics (Brunel):** On-Time Shipment Rate, Freight Cost, Warehouse Cost, Ship-Ahead Rate, Logistics Landed Cost

These domains share no common keys. Cross-domain joins are not fabricated.

## Architecture

```
Source Data (TPC-H + Brunel)
       ↓
Validated Data Layer (RAW + RAW_BRUNEL schemas)
       ↓
Business Entities & Relationships (12 logical tables, 11 relationships)
       ↓
SUPPLY_CHAIN_V2 (Governed semantic view: 16 metrics, 18 verified queries)
       ↓
SUPPLY_CHAIN_AGENT (Cortex Agent VERSION$2, two-domain routing)
       ↓
Streamlit Application (Metal Black enterprise UI)
```

### Snowflake Objects

| Object | FQN |
|---|---|
| Database | `SUPPLYLENS` |
| Commercial tables | `SUPPLYLENS.RAW.*` (8 TPC-H tables: 150K customers, 1.5M orders, 6M line items) |
| Logistics tables | `SUPPLYLENS.RAW_BRUNEL.*` (7 tables + 3 staging views: 19 plants, 9,215 orders) |
| Semantic view | `SUPPLYLENS.ANALYTICS.SUPPLY_CHAIN_V2` |
| Cortex Agent | `SUPPLYLENS.ANALYTICS.SUPPLY_CHAIN_AGENT` (VERSION$2) |

### Semantic Model

| Component | Count |
|---|---|
| Logical tables | 12 (8 commercial + 4 logistics) |
| Relationships | 11 (8 commercial + 3 logistics, zero cross-domain) |
| Dimensions | 36 |
| Facts | 25 |
| Metrics | 16 (14 base + 2 derived) |
| Verified queries | 18 (10 commercial + 8 logistics) |

### Ontology

**Commercial (TPC-H):**
```
Region → Nation → Supplier → PartSupplier ← Part
                    ↓
Nation → Customer → Orders → LineItem → PartSupplier
```

**Logistics (Brunel):**
```
Plant → Product (eligibility)
Plant → Port (shipping links)
Plant → LogisticsOrder → Carrier → FreightRate
```

### Domain Boundary Governance

When a metric exists in only one domain, the system refuses the question for the other domain rather than substituting a different metric. "What is the revenue for logistics?" returns a governed refusal — not commercial revenue mislabeled as logistics.

## Setup

### Prerequisites

- Snowflake account with the SUPPLYLENS database, semantic view, and Cortex Agent already provisioned
- Snowflake CLI configured with a named connection in `~/.snowflake/connections.toml`
- Python 3.10+

### Installation

```bash
git clone <this-repo>
cd supplylens
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml` and set your Snowflake connection name:

```toml
[snowflake]
connection_name = "your-connection-name"
warehouse = "COMPUTE_WH"
```

The `connection_name` must match an entry in `~/.snowflake/connections.toml`. This inherits whatever authenticator the CLI uses (OAuth, externalbrowser, etc.).

### Run

```bash
streamlit run app.py
```

The app launches at `http://localhost:8501`.

## Data Sources

### Commercial Domain: TPC-H

The commercial domain uses the [TPC-H benchmark](https://www.tpc.org/tpch/) dataset (scale factor 1), materialized from `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1` into `SUPPLYLENS.RAW`. This is standard Snowflake sample data available in all accounts.

### Logistics Domain: Brunel

The logistics domain uses the [Brunel Supply Chain Logistics Problem Dataset](https://brunel.figshare.com/articles/dataset/Supply_Chain_Logistics_Problem_Dataset/7558679) (DOI: 10.17633/rd.brunel.7558679), licensed under **CC BY 4.0**.

The `data/` directory contains the source XLSX and extracted CSVs used for ingestion into `SUPPLYLENS.RAW_BRUNEL`. The dataset includes 7 sheets: OrderList (9,215 orders), FreightRates (1,540 rate entries), WhCosts (19 plants), WhCapacities (19 plants), ProductsPerPlant (2,036 entries), VmiCustomers (14 entries), PlantPorts (22 entries).

**Citation:** Willems, S. P. (2019). Supply Chain Logistics Problem Dataset. Brunel University London. https://doi.org/10.17633/rd.brunel.7558679

## Development Workflow

This project was built entirely within **Snowflake Cortex Code (CoCo) CLI** sessions. CoCo was used for:

- **Planning:** Dataset investigation, entity identification, ontology design, domain separation architecture
- **Data engineering:** Schema creation, table DDL, CSV ingestion via COPY INTO, staging view development
- **Semantic modeling:** CREATE SEMANTIC VIEW DDL (~400 lines), metric definitions, verified queries, AI instructions
- **Agent configuration:** ALTER AGENT specification, version management, domain routing instructions
- **Application development:** Streamlit app (1,312 lines), Agent REST API integration, multi-result parser, Metal Black UI
- **Testing:** 18 verified query validations, 12 Agent test questions, domain boundary testing, regression testing
- **Documentation:** All markdown files authored within CoCo

CoCo tools used: `sql_execute`, `bash`, `read`/`write`/`edit`, `web_fetch`, `cortex analyst query`, `cortex agents describe`, `cortex search docs`, and subagent delegation.

The development process is documented in [COCO_DEVELOPMENT_JOURNAL.md](COCO_DEVELOPMENT_JOURNAL.md) with evidence extracted from CoCo session transcripts.

## Documentation

| Document | Description |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Technical architecture, data model, metrics, governance |
| [VALIDATION.md](VALIDATION.md) | 12-test validation report with reference numbers |
| [COCO_PROVENANCE.md](COCO_PROVENANCE.md) | CoCo CLI usage across all project phases |
| [COCO_DEVELOPMENT_JOURNAL.md](COCO_DEVELOPMENT_JOURNAL.md) | Chronological development journal with session evidence |
| [DEMO_SCRIPT.md](DEMO_SCRIPT.md) | 3–5 minute judge-facing demo script |
| [ARCHITECTURE_DIAGRAM.md](ARCHITECTURE_DIAGRAM.md) | Mermaid architecture diagrams |

## Limitations

| Limitation | Detail |
|---|---|
| Single-date logistics snapshot | All Brunel orders are dated 2013-05-26 |
| Freight cost coverage | 75.87% of non-CRF orders have matched freight rates; remainder is NULL |
| CRF orders | Customer-arranged freight has no carrier rate data |
| No logistics revenue | Brunel contains no pricing or sales data |
| No cross-domain keys | TPC-H and Brunel share no common identifiers |
| Plant utilization >100% | Historical routing exceeded target capacity constraints |
| Agent CTE aliasing | Agent occasionally references unaliased columns in CTEs; self-corrects on retry |

## Sources & References

| Source | Description | License |
|---|---|---|
| [Brunel Supply Chain Logistics Problem Dataset](https://brunel.figshare.com/articles/dataset/Supply_Chain_Logistics_Problem_Dataset/7558679) | Logistics operations data | CC BY 4.0 |
| [TPC-H Benchmark](https://www.tpc.org/tpch/) | Commercial order data | TPC Fair Use |
| [Snowflake Semantic Views](https://docs.snowflake.com/en/user-guide/views-semantic/overview) | Governed business definitions | — |
| [Snowflake Cortex Agents](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents) | Natural-language query agent | — |
| [Snowflake Cortex Code CLI](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-cli) | Development environment | — |
| [CoCo CLI Hackathon (GCC Edition)](https://hack2skill.com/event/cococlihack-gccedition/) | Hackathon event page | — |
