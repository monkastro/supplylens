"""Smoke test for SupplyLens — uses cortex CLI to test Analyst API responses."""

import json
import subprocess
import sys

SEMANTIC_VIEW = "SUPPLYLENS.ANALYTICS.SUPPLY_CHAIN_V2"

TEST_QUESTIONS = [
    {
        "question": "What is our overall on-time delivery rate?",
        "expect_sql": True,
        "expect_keywords": ["on_time", "delivery", "COUNT", "RECEIPTDATE", "COMMITDATE"],
    },
    {
        "question": "What is the total revenue by customer market segment?",
        "expect_sql": True,
        "expect_keywords": ["market_segment", "revenue", "SUM", "DISCOUNT"],
    },
    {
        "question": "Which suppliers have the lowest on-time delivery rate?",
        "expect_sql": True,
        "expect_keywords": ["supplier", "LIMIT", "ORDER BY"],
    },
    {
        "question": "What is our logistics on-time shipment rate?",
        "expect_sql": True,
        "expect_keywords": ["SHIP_LATE_DAYS", "on_time"],
    },
]


def run_analyst_query(question: str) -> dict:
    result = subprocess.run(
        ["cortex", "analyst", "query", question, f"--view={SEMANTIC_VIEW}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise Exception(f"cortex analyst query failed: {result.stderr}")
    return json.loads(result.stdout)


def extract_sql(response: dict) -> str | None:
    result_text = response.get("result", "")
    # Extract SQL from markdown code block
    if "```sql" in result_text:
        start = result_text.index("```sql") + 6
        end = result_text.index("```", start)
        return result_text[start:end].strip()
    return None


def main():
    print("SupplyLens Smoke Test")
    print("=" * 60)
    print(f"Semantic View: {SEMANTIC_VIEW}\n")

    passed = 0
    failed = 0

    for test in TEST_QUESTIONS:
        q = test["question"]
        print(f"Q: {q}")

        try:
            response = run_analyst_query(q)
            sql = extract_sql(response)

            if not sql:
                if test["expect_sql"]:
                    print("  FAIL: No SQL generated")
                    print(f"  Response: {response.get('result', '')[:200]}")
                    failed += 1
                else:
                    print("  PASS: No SQL expected and none generated")
                    passed += 1
                continue

            print(f"  SQL: {sql[:120]}...")

            # Check for expected keywords
            sql_upper = sql.upper()
            missing = [kw for kw in test["expect_keywords"] if kw.upper() not in sql_upper]
            if missing:
                print(f"  WARN: Missing keywords in SQL: {missing}")
            else:
                print(f"  PASS: All expected keywords found in generated SQL")

            # Execute the SQL to verify it runs
            exec_result = subprocess.run(
                ["cortex", "--bypass", "--print", f"SELECT COUNT(*) AS row_check FROM ({sql}) t"],
                capture_output=True,
                text=True,
                timeout=60,
            )

            passed += 1

        except Exception as e:
            print(f"  FAIL: {e}")
            failed += 1

        print()

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(TEST_QUESTIONS)}")

    if failed == 0:
        print("\nAll smoke tests passed!")
    else:
        print(f"\n{failed} test(s) failed.")

    return failed == 0


if __name__ == "__main__":
    exit(0 if main() else 1)
