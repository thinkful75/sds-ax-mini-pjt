"""PortfolioLens 채점 스크립트: test_queries.csv 문항마다 에이전트를 돌려 자동 채점하고 결과를 파일로 남긴다.

자동 채점 3가지
1. 도구: expected_tools의 도구를 모두 불렀는가
2. 금지: forbidden 항목 문구가 답변에 그대로 들어 있지 않은가 + 질문에 넣은 계좌번호(10자리 이상)를 답변에 다시 적지 않았는가
3. 숫자: 문항별 핵심 숫자가 expected_values.json 정답과 맞게 답변에 나왔는가(금액 상대오차 ±0.1%, % 는 ±0.1%p)
expected_traits는 자동 채점이 어려우므로 답변과 나란히 결과 파일에 남겨 사람이 확인한다.

RAGAS(--ragas): expected_values.json의 문서 질문 6개를 에이전트에 넣고 context_recall · context_precision ·
faithfulness · answer_relevancy를 낸다. 채점 모델은 .env의 JUDGE_MODEL_ID(기본 Haiku 4.5), 임베딩은 Titan.

사용법: python evaluation/run_eval.py --round 1 [--ids 1,2,3]
        python evaluation/run_eval.py --ragas
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
    called = [t["step"] for t in result["trace"]]
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
        "answer": answer, "contexts": result["contexts"], "trace": result["trace"],
    }


def _import_ragas():
    """RAGAS를 불러온다.

    ragas 0.4.3은 langchain-community의 Vertex AI 모듈을 바로 import하는데, 설치된 langchain-community 0.4.x에는
    그 모듈이 없다. 이 프로젝트는 Vertex AI를 쓰지 않으므로 빈 자리표시 모듈을 끼워 넣고 불러온다.
    """
    import types

    import langchain_community.llms as community_llms

    for name, attr in [("langchain_community.chat_models.vertexai", "ChatVertexAI"),
                       ("langchain_community.llms.vertexai", "VertexAI")]:
        try:
            __import__(name)
        except ImportError:
            stub = types.ModuleType(name)
            setattr(stub, attr, type(attr, (), {}))
            sys.modules[name] = stub
    if not hasattr(community_llms, "VertexAI"):
        community_llms.VertexAI = sys.modules["langchain_community.llms.vertexai"].VertexAI
    import ragas
    return ragas


def run_ragas() -> None:
    """문서 질문 6개로 RAGAS 4개 지표를 계산해 출력하고 evaluation/ragas_results.json에 저장한다."""
    import os
    import warnings

    warnings.filterwarnings("ignore", category=DeprecationWarning)
    ragas = _import_ragas()
    from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall, ResponseRelevancy

    region = os.environ["AWS_REGION"]
    judge = LangchainLLMWrapper(ChatBedrockConverse(
        model_id=os.environ.get("JUDGE_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"),
        region_name=region, temperature=0, max_tokens=4096))
    embeddings = LangchainEmbeddingsWrapper(BedrockEmbeddings(model_id="amazon.titan-embed-text-v2:0", region_name=region))

    samples, rows = [], []
    for s in EXPECTED["ragas"]["samples"]:
        result = answer_question(s["question"])
        contexts = [c["text"] for c in result["contexts"]] or ["(검색된 근거 없음)"]
        samples.append({"user_input": s["question"], "retrieved_contexts": contexts,
                        "response": result["answer"], "reference": s["reference"]})
        rows.append({"id": s["id"], "question": s["question"], "steps": [t["step"] for t in result["trace"]],
                     "answer": result["answer"], "context_doc_ids": [c["doc_id"][:60] for c in result["contexts"]]})
        print(f"[답변 생성] {s['id']:>2} {s['question']}", flush=True)

    metrics = [LLMContextRecall(), LLMContextPrecisionWithReference(), Faithfulness(), ResponseRelevancy()]
    scores = ragas.evaluate(ragas.EvaluationDataset.from_list(samples), metrics=metrics,
                            llm=judge, embeddings=embeddings, show_progress=False)
    frame = scores.to_pandas()
    names = {"context_recall": "context_recall", "llm_context_precision_with_reference": "context_precision",
             "faithfulness": "faithfulness", "answer_relevancy": "answer_relevancy"}
    summary = {label: round(float(frame[col].mean()), 2) for col, label in names.items() if col in frame}
    for row, (_, rec) in zip(rows, frame.iterrows()):
        row.update({label: (None if rec[col] != rec[col] else round(float(rec[col]), 2))
                    for col, label in names.items() if col in frame})

    out = EVAL_DIR / "ragas_results.json"
    out.write_text(json.dumps({"summary": summary, "samples": rows,
                               "judge_model": os.environ.get("JUDGE_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\nRAGAS 평균:", summary)
    print("결과 파일:", out.relative_to(ROOT))


def main() -> None:
    """문항을 돌려 카테고리별·전체 통과율을 출력하고 결과를 파일로 저장한다."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", default="1")
    parser.add_argument("--ids", default="", help="일부 문항만 돌릴 때 쉼표로 구분한 id")
    parser.add_argument("--ragas", action="store_true", help="문서 질문으로 RAGAS 지표만 계산한다")
    args = parser.parse_args()
    if args.ragas:
        run_ragas()
        return

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
