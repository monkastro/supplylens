# SupplyLens — CoCo Provenance

This document records how Snowflake Cortex Code (CoCo) CLI was used throughout the SupplyLens project. Every phase — planning, development, execution, testing, and documentation — was conducted within CoCo sessions.

## Planning

CoCo was used for all architectural planning and design decisions:

- **Dataset investigation:** Fetched and analyzed the Brunel Supply Chain Logistics Problem Dataset (Figshare DOI:10.17633/rd.brunel.7558679) to assess schema, data quality, join feasibility, and metric potential before any code was written.
- **Ontology design:** Designed the two-domain supply-chain ontology (commercial + logistics) with explicit domain boundaries, determining which entities, relationships, and metrics could be supported by each dataset.
- **Staging layer design:** Designed `V_LOGISTICS_ORDERS` (enrichment with warehouse cost, freight cost via carrier rate matching, and timeliness flags) and `V_PLANTS` (combined plant dimension) as the bridge between raw Brunel data and the semantic view.
- **Freight rate matching strategy:** Designed the weight-range join approach with `ROW_NUMBER()` dedup to handle overlapping rate-band boundaries.
- **Semantic view specification:** Planned all 12 logical tables, 11 relationships, 16 metrics, 25 facts, 36 dimensions, and 18 verified queries for `SUPPLY_CHAIN_V2`.
- **Domain boundary governance:** Designed the multi-layer defense (metric comments, AI_SQL_GENERATION rules, AI_QUESTION_CATEGORIZATION entries) after discovering that Cortex Analyst's metric matching layer does not fully reason about domain qualifiers.
- **Agent specification:** Planned VERSION$2 of `SUPPLY_CHAIN_AGENT` with two-domain routing instructions.
- **Application architecture:** Planned the phased migration of the Streamlit app from direct Analyst API to Cortex Agent REST API, including the multi-result parser for cross-domain comparisons.

## Development

All code was written within CoCo sessions:

- **DDL authoring:** All `CREATE TABLE`, `CREATE VIEW`, `CREATE OR ALTER SEMANTIC VIEW`, `ALTER AGENT` statements were authored in CoCo and executed via `sql_execute`.
- **Data ingestion:** Brunel dataset (7 CSV sheets from Excel) was staged to `BRUNEL_STAGE` and loaded via `COPY INTO` commands authored and executed in CoCo.
- **Staging views:** `V_LOGISTICS_ORDERS` and `V_PLANTS` SQL was written in CoCo, including the freight-rate range join, warehouse-cost join, and timeliness computation.
- **Semantic view DDL:** The complete `CREATE SEMANTIC VIEW SUPPLY_CHAIN_V2` (~400 lines) was authored in CoCo, including all table definitions, relationships, facts, dimensions, metrics, AI instructions, and verified queries.
- **Agent specification:** The VERSION$2 YAML specification for `SUPPLY_CHAIN_AGENT` was authored and applied via `ALTER AGENT MODIFY LIVE VERSION SET SPECIFICATION` in CoCo.
- **Domain boundary fix:** The `CREATE OR ALTER SEMANTIC VIEW` patch adding "COMMERCIAL DOMAIN ONLY" to metric comments, DOMAIN BOUNDARY RULES to AI_SQL_GENERATION, and boundary entries to AI_QUESTION_CATEGORIZATION was authored and applied in CoCo.
- **Streamlit application:** All 1,312 lines of `app.py` were written and iteratively refined across multiple CoCo sessions, including:
  - Agent REST API integration (`call_agent()`)
  - Multi-result response parser (`parse_response()`)
  - Metal Black CSS theme (~180 lines)
  - KPI cards, comparison cards, data tables, Altair charts
  - Sidebar architecture panels
  - Domain detection and guardrail response handling
  - Metric definitions dictionary with 12 entries

## Execution

All Snowflake operations were executed through CoCo:

- **`sql_execute` tool:** Used for all DDL (CREATE TABLE, CREATE VIEW, CREATE SEMANTIC VIEW, ALTER AGENT), DML (COPY INTO, INSERT), and validation queries.
- **`cortex analyst query` CLI:** Used to test semantic view verified queries and ad-hoc questions against the semantic model.
- **`cortex agents describe/list` CLI:** Used to inspect agent configuration, version status, and specification.
- **`cortex search docs` CLI:** Used to look up correct syntax for `ALTER AGENT SET SPECIFICATION` and semantic view DDL (discovered that `GET_DDL('VIEW', ...)` fails for semantic views; correct form is `GET_DDL('SEMANTIC VIEW', ...)`).
- **`bash` tool:** Used for Streamlit execution (`streamlit run app.py`), file operations, and CLI commands.
- **`web_fetch` tool:** Used to retrieve the Brunel dataset metadata and documentation from Figshare.

## Testing

All testing was conducted within CoCo:

- **Verified query validation:** All 18 verified queries were executed via `cortex analyst query` to confirm correct SQL generation and result values.
- **Agent testing:** 12 test questions (5 commercial, 4 logistics, 2 domain boundary, 1 cross-domain) were submitted through the Agent REST API and validated.
- **Domain boundary testing:** Specific adversarial questions ("What is the revenue for logistics?", "What is the fill rate for the logistics dataset?") were tested to confirm governed refusals.
- **Cross-domain comparison testing:** "Compare commercial on-time delivery rate with logistics on-time shipment rate" was tested to validate multi-result parsing and comparison rendering.
- **Regression testing:** After each fix (freight rate boundaries, domain boundary strengthening, parser update), all previously passing tests were re-executed.
- **UI smoke testing:** Manual testing of the Streamlit application was conducted with CoCo running the `streamlit run` process and the user verifying results in the browser.
- **Data validation:** Row counts, NULL rates, coverage percentages, and metric values were validated via direct SQL queries in CoCo.

## Documentation

All project documentation was authored within CoCo:

- **ARCHITECTURE.md:** Technical architecture document.
- **VALIDATION.md:** 12-test validation report with reference numbers.
- **COCO_PROVENANCE.md:** This document.
- **DEMO_SCRIPT.md:** Judge-facing demo script.
- **ARCHITECTURE_DIAGRAM.md:** Mermaid architecture diagrams.
- **README.md:** Updated with Sources & References section.

## Tools Used

| CoCo Tool | Purpose |
|---|---|
| `sql_execute` | DDL, DML, queries against Snowflake |
| `bash` | Streamlit execution, CLI commands, file operations |
| `read` / `write` / `edit` | File creation and modification |
| `web_fetch` | External dataset metadata retrieval |
| `cortex analyst query` | Semantic view query testing |
| `cortex agents describe` | Agent inspection |
| `cortex search docs` | Snowflake documentation lookup |
| `task` (subagents) | Parallel exploration and research |
| `system_todo_write` | Task tracking and progress management |

## Session Phases

| Phase | Description | Key Deliverables |
|---|---|---|
| Investigation | Brunel dataset analysis, schema mapping, feasibility assessment | Dataset approval, ingestion plan |
| Ingestion | Stage, file format, COPY INTO for 7 Brunel tables | 7 tables in RAW_BRUNEL, validated row counts |
| Staging | V_LOGISTICS_ORDERS, V_PLANTS enrichment views | Freight matching, warehouse cost, timeliness flags |
| Semantic View | SUPPLY_CHAIN_V2 with 12 tables, 16 metrics, 18 VQs | Governed two-domain semantic model |
| Agent | VERSION$2 specification with domain routing | Two-domain Cortex Agent |
| Governance Fix | Domain boundary strengthening | Metric comments, AI rules, categorization entries |
| App Phase 1 | Agent REST API integration | call_agent(), parse_response() |
| App Phase 2 | Logistics content, metrics, keywords | Dual-domain suggested questions and definitions |
| App Phase 2.5 | Multi-result parser fix | Cross-domain comparison rendering |
| App Phase 3 | Metal Black UI, sidebar architecture, production polish | Enterprise-grade demo candidate |
| Phase 4 | Documentation | ARCHITECTURE, VALIDATION, PROVENANCE, DEMO, DIAGRAM, README |

## No External Tools

No code was written outside of CoCo sessions. No SQL was executed outside of CoCo. No external IDEs, notebooks, or SQL clients were used. The entire project — from dataset investigation through final documentation — was conducted within the Snowflake Cortex Code CLI.
