# SupplyLens Demo Script

**Duration:** 3–5 minutes
**Audience:** Hackathon judges
**Prerequisites:** Streamlit app running, Snowflake connection active

---

## Opening (30 seconds)

> "Supply chain data lives in separate systems. Commercial orders in one place, logistics operations in another. Different teams, different definitions, different answers to the same question. SupplyLens solves this with a governed semantic model that spans two independent data domains — so everyone gets the same trusted answer."

---

## Question 1: Commercial On-Time Delivery

> Ask: **"What is our overall on-time delivery rate?"**

**Expected result:** 37.83%

**Talking points:**
- This queries 6 million line items from TPC-H data
- The metric definition (receipt ≤ commit date, fulfilled orders only) is governed in the semantic view
- Every user asking this question gets the same formula, same filters, same answer
- Show the generated SQL in the expander to demonstrate transparency

---

## Question 2: Logistics On-Time Shipment

> Ask: **"What is our logistics on-time shipment rate?"**

**Expected result:** 97.92%

**Talking points:**
- This queries a completely different dataset (Brunel University logistics data)
- Different source, different schema, different definition of "on-time" (ship-late-days = 0)
- The Agent routes the question to the correct domain automatically
- Note the domain badge changes from Commercial to Logistics

---

## Question 3: Domain Boundary Governance

> Ask: **"What is the revenue for logistics?"**

**Expected result:** Governed refusal with explanation

**Talking points:**
- This is the most important question in the demo
- Revenue is a commercial metric — the logistics dataset has no pricing data
- Without governance, a naive system would return commercial revenue and label it "logistics"
- SupplyLens refuses the question and explains why, rather than giving a wrong answer
- **"A governed refusal is better than a plausible lie"**

---

## Question 4: Cross-Domain Comparison

> Ask: **"Compare commercial on-time delivery rate with logistics on-time shipment rate."**

**Expected result:** Side-by-side comparison showing Commercial 37.83% vs Logistics 97.92%

**Talking points:**
- The Agent queries each domain independently — no fabricated cross-domain join
- Two separate SQL statements, two separate result sets, combined in the UI
- The Agent explains the different definitions of "on-time" in each domain
- This is how cross-domain analytics should work: transparent, honest, no hidden assumptions

---

## Question 5: Analytical Depth

> Ask: **"Show total revenue by region."**

**Expected result:** Five regions with revenue figures

**Talking points:**
- Demonstrates the model handles multi-table joins (LINEITEM → ORDERS → CUSTOMER → NATION → REGION)
- Chart renders automatically with the Metal Black theme
- The semantic view governs the join path — users don't need to know the schema

---

## Closing (30 seconds)

> "SupplyLens is not just a chat interface. It's a governed data layer that ensures consistent answers across independent supply chain domains. The semantic view defines the business ontology. The Cortex Agent translates questions into governed SQL. Domain boundaries prevent cross-contamination. And when a question can't be answered correctly, the system says so — because trust matters more than an answer."

**Key points to emphasize if time allows:**
- Two real-world datasets with no fabricated links
- 16 governed metrics, 18 verified queries
- Domain boundary enforcement at the semantic layer
- Built entirely within CoCo CLI — no external tools
- Brunel dataset: CC BY 4.0 licensed, DOI-referenced

---

## Backup Questions

If judges ask follow-up questions:

| Question | Expected Behavior |
|---|---|
| "How many orders do we have?" | 1,500,000 (commercial) |
| "Which plants have the highest warehouse cost?" | Plant ranking from Brunel data |
| "What is the fill rate for logistics?" | Governed refusal (fill rate is commercial only) |
| "Show top 10 suppliers by revenue" | Supplier ranking with correct join path |
| "What percentage of shipments were ahead of schedule?" | 51.97% (logistics ship-ahead rate) |

---

## If Something Goes Wrong

- **Agent timeout:** The app has a 30-second timeout. Retry the question — Agent self-corrects on CTE alias issues.
- **NULL freight costs:** Expected for CRF (customer-arranged freight) orders. Explain that missing data is shown as NULL, never imputed.
- **Plant utilization >100%:** Expected. Historical routing exceeded target capacity constraints. This is documented behavior, not a bug.
