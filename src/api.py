"""PortfolioLens API 서버: POST /query로 question을 받아 answer, contexts, trace를 돌려준다."""

import re
import sys
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent import run_query

# 하이픈으로 이어진 숫자 묶음(예: 110-123-456789) 또는 하이픈 없는 10자리 이상 숫자
ACCOUNT_LIKE = re.compile(r"(?<![\d*])\d{2,6}(?:-\d{2,8}){1,3}(?![\d])|(?<![\d,.*])\d{10,}(?![\d,])")


class QueryRequest(BaseModel):
    """요청 본문. question 필드 하나를 읽는다."""

    question: str


class QueryResponse(BaseModel):
    """응답 본문. answer, contexts, trace 세 키로 돌려준다."""

    answer: str
    contexts: list[str]
    trace: list[dict]


def mask_account_numbers(text: str) -> str:
    """답변에 남은 계좌번호처럼 보이는 숫자(10자리 이상)를 끝 4자리만 남기고 가린다. 날짜는 건드리지 않는다."""

    def _mask(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "****" + digits[-4:] if len(digits) >= 10 else m.group(0)

    return ACCOUNT_LIKE.sub(_mask, text)


def answer_question(question: str) -> dict:
    """에이전트를 실행하고 답변의 계좌번호를 가려 응답 형식으로 돌려준다."""
    result = run_query(question)
    result["answer"] = mask_account_numbers(result["answer"])
    return QueryResponse(**result).model_dump()


app = FastAPI(title="PortfolioLens")


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> dict:
    """질문을 받아 에이전트 답변과 근거, 도구 호출 기록을 돌려준다."""
    return answer_question(req.question)
