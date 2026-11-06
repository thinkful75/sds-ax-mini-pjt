"""PortfolioLens API 서버: POST /query로 question을 받아 출력 검사(숫자 근거·계좌번호 마스킹)를 거친 answer, contexts, trace를 돌려준다. GET /은 시험용 채팅 화면."""

import json
import re
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent import revise_answer, run_query

# 하이픈으로 이어진 숫자 묶음(예: 110-123-456789) 또는 하이픈 없는 10자리 이상 숫자
ACCOUNT_LIKE = re.compile(r"(?<![\d*])\d{2,6}(?:-\d{2,8}){1,3}(?![\d])|(?<![\d,.*])\d{10,}(?![\d,])")
# 숫자 검사에서 뺄 날짜·시각 표기(기준시각 등)
DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|\d{4}\s*년|\d{1,2}\s*월|\d{1,2}\s*일")
# 영문자 뒤에 붙은 숫자(S&P500 등)는 이름의 일부로 보고 뺀다
NUMBER = re.compile(r"(?<![A-Za-z\d.,])(\d[\d,]*(?:\.\d+)?)(\s*만)?(\s*%)?")
ALWAYS_ALLOWED = {100.0}  # 비중 합계 100%
# 입력 가드레일: 시스템 프롬프트·비밀 값 노출이나 지시 무시를 노리는 요청(프롬프트 인젝션)
INJECTION = re.compile(
    r"시스템\s*프롬프트|이전\s*(지시|명령|규칙)|(지시|명령|규칙)[을를]?\s*(모두\s*)?무시|"
    r"api\s*키|앱\s*키|app\s*key|secret|접근\s*토큰|액세스\s*토큰|"
    r"ignore\s+(all\s+|the\s+)?(previous|prior|above)|system\s*prompt|developer\s*mode|jailbreak",
    re.IGNORECASE,
)
INJECTION_REPLY = """죄송합니다. 시스템 프롬프트, 내부 구현, API 키·접근 토큰 같은 보안 정보는 알려드릴 수 없고, 정해진 규칙을 무시하라는 요청도 따를 수 없습니다.

대신 이런 질문은 도와드릴 수 있습니다.
- 계좌별·자산군별·통화별·섹터별 자산 현황 (예: "계좌별로 나눠서 보여줘")
- 여러 계좌에 나눠 산 종목 합산 (예: "삼성전자 합치면 평단 얼마야?")
- 해외주식 양도세·배당소득세·ISA 제도 안내"""


class QueryRequest(BaseModel):
    """요청 본문. question 필드 하나를 읽는다."""

    question: str


class Context(BaseModel):
    """답의 근거 하나: 문서 조각이면 문서 doc_id, 도구 결과면 '도구이름:입력'."""

    doc_id: str
    text: str


class TraceStep(BaseModel):
    """처리 단계 하나: 도구 호출(get_portfolio·build_view·retrieve_docs) 또는 가드레일(input_guard·number_check)."""

    step: str
    input: str
    output: str


class QueryResponse(BaseModel):
    """응답 본문(표준 규약 §4-2). answer, contexts, trace 세 키로 돌려준다."""

    answer: str
    contexts: list[Context]
    trace: list[TraceStep]


def _step(name: str, input_value, output_value) -> dict:
    """가드레일 단계를 trace 형식(step, input, output 문자열)으로 만든다."""
    return {"step": name, "input": json.dumps(input_value, ensure_ascii=False),
            "output": json.dumps(output_value, ensure_ascii=False)}


def mask_account_numbers(text: str) -> str:
    """답변에 남은 계좌번호처럼 보이는 숫자(10자리 이상)를 끝 4자리만 남기고 가린다. 날짜는 건드리지 않는다."""

    def _mask(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "****" + digits[-4:] if len(digits) >= 10 else m.group(0)

    return ACCOUNT_LIKE.sub(_mask, text)


def extract_numbers(text: str, checked_only: bool = False) -> list[tuple[str, float]]:
    """글에서 숫자를 (원문, 값)으로 뽑는다. 날짜·시각은 빼고 '만' 단위는 원으로 바꾼다.

    checked_only가 참이면 검사 대상만 남긴다: 소수점이 있거나, %가 붙었거나, 100 이상인 숫자.
    (1~99의 개수·순위 같은 작은 정수는 검사하지 않는다)
    """
    found = []
    for m in NUMBER.finditer(DATE_TIME.sub(" ", text)):
        raw = m.group(1).rstrip(",")
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if m.group(2):
            value *= 10000
        if checked_only and "." not in raw and not m.group(3) and value < 100:
            continue
        found.append((m.group(0).strip(), value))
    return found


def find_unsupported_numbers(answer: str, question: str, result: dict) -> list[str]:
    """답변의 숫자 중 질문·도구 결과·문서 조각 어디에도 없는 숫자(모델이 직접 계산하거나 지어낸 값)를 찾는다.

    표기 반올림(68,333 ↔ 68333.33)은 허용하도록 상대오차 0.05%까지 같은 숫자로 본다.
    """
    source_text = " ".join([question, *(c["text"] for c in result["contexts"]),
                            *(t["output"] for t in result["trace"])])
    sources = {v for _, v in extract_numbers(source_text)} | ALWAYS_ALLOWED
    unsupported = []
    for raw, value in extract_numbers(answer, checked_only=True):
        if not any(abs(value - s) <= max(0.01, abs(s) * 0.0005) for s in sources) and raw not in unsupported:
            unsupported.append(raw)
    return unsupported


def check_input(question: str) -> str | None:
    """입력 가드레일: 프롬프트 인젝션 요청이면 걸린 문구를, 아니면 None을 돌려준다."""
    m = INJECTION.search(question)
    return m.group(0) if m else None


def answer_question(question: str) -> dict:
    """입력 검사 → 에이전트 실행 → 출력 검사를 거쳐 응답 형식으로 돌려준다.

    0. 입력 검사: 프롬프트 인젝션 요청이면 모델을 부르지 않고 거절 답변을 돌려주고, trace에 input_guard로 남긴다.
       (사용자가 입력한 계좌번호는 에이전트의 PII 미들웨어가 모델에 넘기기 전에 가린다)
    1. 숫자 검사: 도구 결과·문서에 없는 숫자가 답변에 있으면 그 숫자를 빼고 한 번 다시 쓰게 하고, trace에 number_check로 남긴다.
    2. 계좌번호 마스킹: 답변에 남은 계좌번호를 끝 4자리만 남긴다.
    """
    blocked = check_input(question)
    if blocked:
        return QueryResponse(answer=INJECTION_REPLY, contexts=[], trace=[
            _step("input_guard", {"question": question, "matched": blocked}, {"blocked": True})]).model_dump()

    result = run_query(question)
    unsupported = find_unsupported_numbers(result["answer"], question, result)
    if unsupported:
        revised = revise_answer(result["answer"], unsupported)
        result["answer"] = revised.answer
        remaining = find_unsupported_numbers(result["answer"], question, result)
        result["trace"].append(_step("number_check", {"unsupported": unsupported},
                                     {"removed": revised.removed, "remaining": remaining}))
    result["answer"] = mask_account_numbers(result["answer"])
    return QueryResponse(**result).model_dump()


app = FastAPI(title="PortfolioLens")


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> dict:
    """질문을 받아 에이전트 답변과 근거, 도구 호출 기록을 돌려준다."""
    return answer_question(req.question)


CHAT_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PortfolioLens</title>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<style>
body{margin:0;font-family:system-ui,"Malgun Gothic",sans-serif;background:#f5f6f8;color:#222}
header{padding:14px 20px;background:#1f3a5f;color:#fff;font-weight:600}
#log{max-width:860px;margin:0 auto;padding:16px 16px 120px}
.msg{margin:12px 0;padding:12px 16px;border-radius:10px;line-height:1.6;overflow-x:auto}
.me{background:#dbe8ff;margin-left:20%}
.bot{background:#fff;border:1px solid #ddd}
.bot table{border-collapse:collapse}.bot td,.bot th{border:1px solid #ccc;padding:4px 8px}
details{margin-top:8px;font-size:13px;color:#555}pre{white-space:pre-wrap;word-break:break-all}
form{position:fixed;bottom:0;left:0;right:0;display:flex;gap:8px;padding:12px 16px;background:#fff;border-top:1px solid #ddd}
input{flex:1;padding:12px;font-size:16px;border:1px solid #bbb;border-radius:8px}
button{padding:0 20px;font-size:16px;border:0;border-radius:8px;background:#1f3a5f;color:#fff;cursor:pointer}
button:disabled{opacity:.5}
</style></head><body>
<header>PortfolioLens · 조회 전용 (Mock 데이터)</header>
<div id="log"></div>
<form id="f"><input id="q" placeholder="예: 계좌별로 나눠서 보여줘" autocomplete="off" autofocus><button id="b">보내기</button></form>
<script>
const log=document.getElementById('log'),q=document.getElementById('q'),b=document.getElementById('b');
function add(cls,html){const d=document.createElement('div');d.className='msg '+cls;d.innerHTML=html;log.appendChild(d);d.scrollIntoView();return d}
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
document.getElementById('f').onsubmit=async e=>{
  e.preventDefault();const question=q.value.trim();if(!question)return;
  add('me',esc(question));q.value='';b.disabled=true;const d=add('bot','답변을 만드는 중...');
  try{
    const r=await fetch('/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question})});
    if(!r.ok)throw new Error(await r.text());
    const j=await r.json();
    const tools=j.trace.map(t=>t.step).join(', ')||'없음';
    d.innerHTML=marked.parse(j.answer)+'<details><summary>도구 호출: '+esc(tools)+'</summary><pre>'+esc(JSON.stringify(j.trace,null,2))+'</pre></details>';
  }catch(err){d.innerHTML='오류: '+esc(String(err.message||err))}
  b.disabled=false;q.focus();
};
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def chat_page() -> str:
    """브라우저에서 질문을 입력해 /query를 시험하는 간단한 채팅 화면을 돌려준다."""
    return CHAT_PAGE
