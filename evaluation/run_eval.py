"""PortfolioLens 채점 스크립트: test_queries.csv 문항마다 에이전트를 돌려 자동 채점하고 결과를 파일로 남긴다.

자동 채점 3가지
1. 도구: expected_tools의 도구를 모두 불렀는가
2. 금지: forbidden 항목 문구가 답변에 그대로 들어 있지 않은가 + 질문에 넣은 계좌번호(10자리 이상)를 답변에 다시 적지 않았는가
3. 숫자: 문항별 핵심 숫자가 expected_values.json 정답과 맞게 답변에 나왔는가(금액 상대오차 ±0.1%, % 는 ±0.1%p)
expected_traits는 자동 채점이 어려우므로 답변과 나란히 결과 파일에 남겨 사람이 확인한다.

사용법: python evaluation/run_eval.py --round 1 [--ids 1,2,3]
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.api import answer_question  # noqa: E402

EVAL_DIR = ROOT / "evaluation"
EXPECTED = json.loads((EVAL_DIR / "expected_values.json").read_text(encoding="utf-8"))
TOL = EXPECTED["meta"]["tolerance"]


def _lens_values(lens: str, field: str) -> list[float]:
    """렌즈의 모든 항목에서 한 필드 값을 모은다."""
    return [row[field] for row in EXPECTED["lenses"][lens]["rows"].values()]


def required_numbers(case_id: str) -> list[tuple[str, float]]:
    """문항별로 답변에 반드시 나와야 하는 핵심 숫자 (이름, 값) 목록."""
    L, C = EXPECTED["lenses"], EXPECTED["cases"]
    samsung = L["symbol"]["rows"]["005930"]
    usd = L["currency"]["rows"]["USD"]
    table = {
        "1": [("총 평가금액", L["account"]["total"]["market_value_krw"])],
        "2": [(f"계좌 평가금액 {k}", v["market_value_krw"]) for k, v in L["account"]["rows"].items()]
             + [(f"계좌 비중 {k}", v["weight_pct"]) for k, v in L["account"]["rows"].items()],
        "3": [(f"자산군 비중 {k}", v["weight_pct"]) for k, v in L["asset_class"]["rows"].items()],
        "4": [("달러 원금액", usd["market_value_usd"]), ("달러 원화 환산액", usd["market_value_krw"]),
              ("달러 비중", usd["weight_pct"])],
        "5": [("합산 수량", samsung["quantity"]), ("가중 평단", samsung["avg_price"]),
              ("합산 평가손익", samsung["unrealized_pnl_krw"])],
        "10": [(f"ISA 섹터 평가금액 {k}", v["market_value_krw"]) for k, v in C["10_isa_sector"]["rows"].items()],
        "11": [("최대 비중 종목", C["11_top"]["symbol_weight_pct"]), ("최대 비중 섹터", C["11_top"]["sector_weight_pct"])],
        "12": [("해외 미실현 이익(원)", C["12_us_listed_unrealized_pnl"]["krw"])],
        "13": [("B증권 원화 환산", C["13_b_broker_krw"]["market_value_krw"])],
    }
    return table.get(case_id, [])


def numbers_in(text: str) -> list[float]:
    """답변에서 숫자를 모두 뽑는다(쉼표 제거, '만원' 단위도 원으로 환산)."""
    nums = []
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(만)?", text):
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        nums.append(value * 10000 if m.group(2) else value)
    return nums


def check_numbers(case_id: str, answer: str) -> tuple[bool, list[str]]:
    """핵심 숫자가 답변에 정답 허용오차 안으로 나왔는지 본다."""
    found = numbers_in(answer)
    missing = []
    for name, expected in required_numbers(case_id):
        is_pct = "비중" in name
        tol = TOL["pct_absolute"] if is_pct else max(abs(expected) * TOL["amount_relative"], 0.01)
        if not any(abs(n - expected) <= tol for n in found):
            missing.append(f"{name}={expected}")
    return not missing, missing


def check_case(row: dict, result: dict) -> dict:
    """문항 하나를 자동 채점한다."""
    answer = result["answer"]
    called = [t["tool"] for t in result["trace"]]
    expected_tools = [t for t in row["expected_tools"].split(";") if t]
    tools_ok = all(t in called for t in expected_tools)

    forbidden_hits = [f.strip() for f in row["forbidden"].split(";") if f.strip() and f.strip() in answer]
    for digits in re.findall(r"\d[\d-]{9,}\d", row["input"]):  # 질문에 넣은 계좌번호 재출력 검사
        only = re.sub(r"\D", "", digits)
        if len(only) >= 10 and (digits in answer or only in re.sub(r"\D", "", answer)):
            forbidden_hits.append(f"입력 계좌번호 재출력({digits})")
    forbidden_ok = not forbidden_hits

    numbers_ok, missing = check_numbers(row["id"], answer)
    return {
        "id": row["id"], "category": row["category"], "input": row["input"],
        "passed": tools_ok and forbidden_ok and numbers_ok,
        "tools_ok": tools_ok, "expected_tools": expected_tools, "called_tools": called,
        "forbidden_ok": forbidden_ok, "forbidden_hits": forbidden_hits,
        "numbers_ok": numbers_ok, "missing_numbers": missing,
        "expected_traits": row["expected_traits"], "forbidden": row["forbidden"],
        "answer": answer,
    }


def main() -> None:
    """문항을 돌려 카테고리별·전체 통과율을 출력하고 결과를 파일로 저장한다."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", default="1")
    parser.add_argument("--ids", default="", help="일부 문항만 돌릴 때 쉼표로 구분한 id")
    args = parser.parse_args()

    rows = list(csv.DictReader((EVAL_DIR / "test_queries.csv").open(encoding="utf-8")))
    if args.ids:
        wanted = set(args.ids.split(","))
        rows = [r for r in rows if r["id"] in wanted]

    results = []
    for row in rows:
        start = time.time()
        try:
            result = answer_question(row["input"])
            graded = check_case(row, result)
        except Exception as exc:  # 한 문항이 실패해도 나머지는 계속 돈다
            graded = {"id": row["id"], "category": row["category"], "input": row["input"], "passed": False,
                      "error": f"{type(exc).__name__}: {exc}", "expected_traits": row["expected_traits"]}
        graded["seconds"] = round(time.time() - start, 1)
        results.append(graded)
        mark = "통과" if graded["passed"] else "실패"
        print(f"[{mark}] {row['id']:>2} {row['category']:<9} {row['input']}", flush=True)

    summary = {}
    for cat in ["positive", "negative", "edge", "guardrail"]:
        group = [r for r in results if r["category"] == cat]
        if group:
            summary[cat] = f"{sum(r['passed'] for r in group)}/{len(group)}"
    summary["total"] = f"{sum(r['passed'] for r in results)}/{len(results)}"

    out = EVAL_DIR / f"round{args.round}_results.json"
    out.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("\n카테고리별 통과율:", summary)
    print("결과 파일:", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
