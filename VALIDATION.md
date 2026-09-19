# SupplyLens Validation Report

## Summary

| Category | Tests | Pass | Fail | Notes |
|---|---|---|---|---|
| Commercial domain queries | 5 | 5 | 0 | Via Cortex Agent |
| Logistics domain queries | 4 | 4 | 0 | Via Cortex Agent |
| Domain boundary governance | 2 | 2 | 0 | Refused with explanation |
| Cross-domain comparison | 1 | 1 | 0 | Two independent result sets combined |
| **Total** | **12** | **12** | **0** | |

All tests executed through the Cortex Agent (`SUPPLY_CHAIN_AGENT` VERSION$2) backed by `SUPPLY_CHAIN_V2` semantic view.

---

## Test 1: Commercial On-Time Delivery Rate

**Question:** "What is our overall on-time delivery rate?"

**Expected behavior:** Query `LINEITEM` for fulfilled orders where receipt date ≤ commit date.

**Result:** 37.83%

**Status:** PASS

**Validation:** Confirmed against verified query VQ-C-OTD:
```sql
SELECT ROUND(AVG(CASE WHEN L_RECEIPTDATE <= L_COMMITDATE THEN 100 ELSE 0 END), 2)
FROM SUPPLYLENS.RAW.LINEITEM
WHERE L_RETURNFLAG IN ('A','N');
```

---

## Test 2: Commercial Fill Rate

**Question:** "What is our overall fill rate?"

**Expected behavior:** Query `LINEITEM` for ratio of non-returned quantity to total quantity.

**Result:** 50.66%

**Status:** PASS

**Validation:** Confirmed against verified query VQ-C-FR:
```sql
SELECT ROUND(SUM(CASE WHEN L_RETURNFLAG IN ('A','N') THEN L_QUANTITY ELSE 0 END)
  / NULLIF(SUM(L_QUANTITY), 0) * 100, 2)
FROM SUPPLYLENS.RAW.LINEITEM;
```

---

## Test 3: Commercial Revenue by Region

**Question:** "Show total revenue by region."

**Expected behavior:** Join `LINEITEM → ORDERS → CUSTOMER → NATION → REGION`, compute `SUM(extended_price * (1 - discount))`.

**Result:** Five regions returned with revenue figures. Agent generates correct join path and metric formula.

**Status:** PASS

---

## Test 4: Commercial Order Count

**Question:** "How many orders do we have?"

**Expected behavior:** `COUNT(*)` on `ORDERS` table.

**Result:** 1,500,000 orders.

**Status:** PASS

---

## Test 5: Commercial Supplier Analysis

**Question:** "Show top 10 suppliers by total revenue."

**Expected behavior:** Join `LINEITEM → PARTSUPP → SUPPLIER`, compute revenue, ORDER BY DESC LIMIT 10.

**Result:** 10 suppliers with revenue figures. Correct join path through PARTSUPP.

**Status:** PASS

---

## Test 6: Logistics On-Time Shipment Rate

**Question:** "What is our logistics on-time shipment rate?"

**Expected behavior:** Query `V_LOGISTICS_ORDERS` for `AVG(CASE WHEN SHIP_LATE_DAYS = 0 THEN 100 ELSE 0 END)`.

**Result:** 97.92%

**Status:** PASS

**Validation:** Confirmed against verified query VQ-L-OTS:
```sql
SELECT ROUND(AVG(CASE WHEN SHIP_LATE_DAYS = 0 THEN 100.0 ELSE 0 END), 2)
FROM SUPPLYLENS.RAW_BRUNEL.V_LOGISTICS_ORDERS;
```

---

## Test 7: Logistics Ship-Ahead Rate

**Question:** "What percentage of logistics shipments were shipped ahead of schedule?"

**Expected behavior:** Query `V_LOGISTICS_ORDERS` for `AVG(CASE WHEN SHIP_AHEAD_DAYS > 0 THEN 100 ELSE 0 END)`.

**Result:** 51.97%

**Status:** PASS

**Validation:** Confirmed against verified query VQ-L-SAR:
```sql
SELECT ROUND(AVG(CASE WHEN SHIP_AHEAD_DAYS > 0 THEN 100.0 ELSE 0 END), 2)
FROM SUPPLYLENS.RAW_BRUNEL.V_LOGISTICS_ORDERS;
```

---

## Test 8: Logistics Freight Cost by Carrier

**Question:** "Show total freight cost by carrier."

**Expected behavior:** Query `V_LOGISTICS_ORDERS`, GROUP BY carrier, SUM freight cost. Exclude NULL freight (CRF / weight-gap orders).

**Result:** Carrier-level freight breakdown returned. CRF carrier shows NULL freight (correct behavior — customer-arranged freight has no rate data).

**Status:** PASS

---

## Test 9: Logistics Plant Analysis

**Question:** "Which plants have the highest warehouse cost per unit?"

**Expected behavior:** Query `V_PLANTS` for cost_per_unit, ordered descending.

**Result:** Plant cost ranking returned with correct values from staged view.

**Status:** PASS

---

## Test 10: Domain Boundary — Revenue for Logistics

**Question:** "What is the revenue for logistics?"

**Expected behavior:** Agent should **refuse** this question. Revenue is a commercial-domain metric (TPC-H). The Brunel logistics dataset contains no pricing or sales data.

**Result:** Agent returned a domain-boundary refusal explaining that revenue is not available in the logistics dataset.

**Status:** PASS (governed refusal)

**Governance note:** This test validates that the Agent does not substitute commercial revenue when the user specifies "for logistics."

---

## Test 11: Domain Boundary — Fill Rate for Logistics

**Question:** "What is the fill rate for the logistics dataset?"

**Expected behavior:** Agent should **refuse**. Fill rate (non-returned / total quantity) is a commercial metric. Brunel has no return status.

**Result:** Agent returned a domain-boundary refusal explaining that fill rate is not available in the logistics domain.

**Status:** PASS (governed refusal)

**Governance note:** Prior to domain-boundary strengthening, Cortex Analyst matched the `fill_rate` metric regardless of the "for logistics" qualifier. Fixed by adding "COMMERCIAL DOMAIN ONLY" to metric comments and explicit DOMAIN BOUNDARY RULES to AI_SQL_GENERATION instructions.

---

## Test 12: Cross-Domain Comparison

**Question:** "Compare commercial on-time delivery rate with logistics on-time shipment rate."

**Expected behavior:** Agent issues two separate SQL queries — one against `LINEITEM` (commercial OTD) and one against `V_LOGISTICS_ORDERS` (logistics OTS). Application parser combines the two result sets into a comparison view.

**Result:**

| Domain | Metric | Value |
|---|---|---|
| Commercial (TPC-H) | On-Time Delivery Rate | 37.83% |
| Logistics (Brunel) | On-Time Shipment Rate | 97.92% |

**Status:** PASS

**Technical note:** The Agent's multi-result response required a custom parser that accumulates all `system_execute_sql` tool_results. When result sets have different schemas, the parser pivots them into a DOMAIN/METRIC/VALUE comparison table. Agent narrative explains the different definitions of "on-time" across the two domains.

---

## Validated Reference Numbers

These numbers are stable and reproducible. They serve as regression anchors.

| Metric | Value | Domain | Coverage |
|---|---|---|---|
| Commercial On-Time Delivery Rate | 37.83% | TPC-H | Fulfilled line items |
| Commercial Fill Rate | 50.66% | TPC-H | All line items |
| Commercial Order Count | 1,500,000 | TPC-H | All orders |
| Logistics On-Time Shipment Rate | 97.92% | Brunel | All 9,215 orders |
| Logistics Ship-Ahead Rate | 51.97% | Brunel | All 9,215 orders |
| Logistics Freight Coverage | 75.87% | Brunel | Non-CRF orders with rate match |
| Logistics Order Count | 9,215 | Brunel | All orders |
| Plant Count | 19 | Brunel | All plants |

---

## Semantic View Verified Queries

The semantic view contains 18 verified queries that serve as golden-path SQL for common questions.

| ID | Domain | Description | Status |
|---|---|---|---|
| VQ-C-OTD | Commercial | Overall on-time delivery rate | PASS |
| VQ-C-FR | Commercial | Overall fill rate | PASS |
| VQ-C-REV | Commercial | Total revenue | PASS |
| VQ-C-RR | Commercial | Return rate | PASS |
| VQ-C-OC | Commercial | Order count | PASS |
| VQ-C-AOV | Commercial | Average order value | PASS |
| VQ-C-REG | Commercial | Revenue by region | PASS |
| VQ-C-TOP10 | Commercial | Top 10 suppliers by revenue | PASS |
| VQ-C-LC | Commercial | Landed cost breakdown | PASS |
| VQ-C-DOI | Commercial | Days of inventory | PASS |
| VQ-L-OTS | Logistics | On-time shipment rate | PASS |
| VQ-L-FC | Logistics | Total freight cost by carrier | PASS |
| VQ-L-WC | Logistics | Total warehouse cost | PASS |
| VQ-L-LLC | Logistics | Logistics landed cost | PASS |
| VQ-L-SAR | Logistics | Ship-ahead rate | PASS |
| VQ-L-PU | Logistics | Plant utilization | PASS |
| VQ-L-PP | Logistics | Products per plant | PASS |
| VQ-L-SP | Logistics | Shipping ports per plant | PASS |

All 18 verified queries passed validation during semantic view creation and agent testing.

---

## Known Limitations Validated

| Limitation | Validated Behavior |
|---|---|
| CRF orders have NULL freight cost | Confirmed: CRF carrier rows show NULL freight in query results |
| Weight-gap orders have NULL freight cost | Confirmed: orders outside rate-band boundaries return NULL |
| Plant utilization can exceed 100% | Confirmed: historical routing data shows plants exceeding target capacity |
| No cross-domain joins | Confirmed: Agent never fabricates joins between TPC-H and Brunel tables |
| Single-date Brunel snapshot | All logistics orders dated 2013-05-26 as expected |
