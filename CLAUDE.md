# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 이 문서는 한국어로 작성한다. 사용자에게 보이는 모든 응답도 한국어로 한다.

## 프로젝트 규칙

### 기술 스택
- Python, LangChain, LangGraph
- 모델은 Amazon Bedrock (`ChatBedrockConverse`)
- 에이전트 생성은 `langchain.agents`의 `create_agent`를 쓴다

### 폴더 구조 (제출 규약. 바꾸지 않는다)
```
src/agent.py       메인 에이전트 그래프
src/tools.py       도메인 도구
src/retriever.py   RAG 파이프라인
data/              사용한 문서와 데이터
evaluation/        test_queries.csv 와 평가 리포트
```

### 주고받는 형식 (제출 규약)
- `POST /query`로 받고 `question` 필드를 읽는다
- 답은 `answer`, `contexts`, `trace` 세 키로 돌려준다

### 코드 규칙
- 파일 하나에 한 가지 역할만 둔다
- 함수와 도구에는 한국어 docstring을 쓴다
- 비밀 값은 `.env`에서 읽고 코드에 적지 않는다

### 하지 말 것
- 요청하지 않은 파일을 새로 만들지 않는다
- 기존 파일을 통째로 다시 쓰지 않는다. 바뀐 부분만 고친다

## 프로젝트 현황

PortfolioLens는 3일짜리 미니 프로젝트다(Day 8 기획 → Day 9 개발 → Day 10 마무리·제출). 여러 증권사에 흩어진 보유 자산을 자연어 질문 하나로 원하는 관점("렌즈")으로 다시 묶어 보여주는 **조회 전용** 에이전트다.

Day 10 기준으로 개선 루프 3회와 2차 평가를 마쳤다(1차 14/16 → 2차 16/16, guardrail 3/3). 과제의 12개 패턴 중 6개 이상 적용 조건에 맞춰 7개(1 LCEL·Pydantic, 2 ReAct, 3 RAG, 4 도구 다중, 6 가드레일, 8 미들웨어, 12 평가·RAGAS)를 적용했다(README "활용한 패턴"). 시스템 프롬프트 개선에 더해, "숫자는 도구 결과만" 규칙을 `src/api.py`의 출력 숫자 검사로 구조화했다(`evaluation/round2_report.md`). 저장소는 https://github.com/thinkful75/sds-ax-mini-pjt (`main`). 작업 전에 다음을 읽는다.
- `PLAN.md`: 확정한 결정, 도구·파일 설계, Mock 데이터 설계, 평가 설계, Day 9~10 일정
- `SERVICE.md`: 사용자·문제, 도구 입출력 계약, 가드레일 정책, 성공 기준
- `evaluation/test_queries.csv`: "완성"의 기준이 되는 16개 질문 세트(positive / negative / edge / guardrail)

`README.md`는 제출용 요약이다. 내용이 이 문서나 위 파일과 다르면 이 문서와 위 파일을 따른다.

## 명령어

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
cp .env.example .env                          # AWS 자격 증명과 리전을 채운다
.venv/Scripts/python src/agent.py "질문"      # 에이전트에 질문 하나 실행(도구 호출 기록과 답변 출력)
.venv/Scripts/python src/tools.py             # 계좌 렌즈 집계 결과 출력(Bedrock 없이 동작)
.venv/Scripts/python -m src.retriever         # 문서 검색 시험
.venv/Scripts/python -m uvicorn src.api:app --port 8000   # http://localhost:8000/ 채팅 화면, POST /query
.venv/Scripts/python evaluation/run_eval.py --round 1            # 16문항 전체 채점
.venv/Scripts/python evaluation/run_eval.py --round 2 --ids 12,14  # 일부 문항만
.venv/Scripts/python evaluation/run_eval.py --ragas              # 문서 질문 6개 RAGAS (채점 모델 JUDGE_MODEL_ID)
```

- 모델은 `.env`의 `BEDROCK_MODEL_ID`(현재 `us.anthropic.claude-sonnet-4-6`)를 쓴다. 강의 계정은 `us.` 추론 프로필 ID로만 호출되고 Sonnet 4.6, Sonnet 4.5, Haiku 4.5만 허용된다. 임베딩은 `amazon.titan-embed-text-v2:0`. RAGAS 채점(`JUDGE_MODEL_ID`)과 검색 리랭킹(`RERANK_MODEL_ID`)은 Haiku 4.5.
- 리전은 `us-west-2`를 쓴다. us-east-1은 하루 토큰 한도(ThrottlingException: Too many tokens per day)에 자주 걸렸고, us-east-2는 Claude 호출 권한이 없다.
- 액세스 키가 AKIA로 시작하는 장기 자격 증명이라 `AWS_SESSION_TOKEN`은 비워 둔다. 코드가 빈 값을 지운다.
- Windows 콘솔에서 한글이 깨지면 `PYTHONIOENCODING=utf-8`을 붙이거나 출력을 파일로 받아 읽는다.

## 아키텍처

질문 → `src/api.py` 입력 가드레일(프롬프트 인젝션이면 모델 호출 없이 거절, trace에 `input_guard`) → 에이전트(`src/agent.py`, 모델 `ChatBedrockConverse`, 시스템 프롬프트에 가드레일 규칙, 미들웨어 `PIIMiddleware`·`ModelRetryMiddleware`) → 도구 호출 → `src/api.py`에서 출력 검사(도구·문서에 없는 숫자가 있으면 한 번 다시 쓰게 하고 trace에 `number_check`로 기록, 그다음 계좌번호 마스킹) → `{answer, contexts, trace}`

파일 구성(정해진 3개 + 추가 2개, 그 밖의 파일은 요청 없이 만들지 않는다):
- `src/agent.py`: 에이전트와 시스템 프롬프트. `python src/agent.py`로 시험 질문을 실행할 수 있게 한다.
- `src/tools.py`: `get_portfolio()`와 `build_view(lens, filter)`. `data/broker/*.json`을 직접 읽는다.
- `src/retriever.py`: `retrieve_docs(query)`. `data/docs/*.md` 대상 Chroma + BM25 하이브리드 검색으로 후보 8개를 뽑고 Haiku 4.5 리랭킹으로 최대 3개를 돌려준다. 각 문서 머리에 `doc_id`/`title`이 있고, 답변은 문서명을 인용해야 한다.
- `src/api.py`: `POST /query`와 `/` 채팅 화면. 출력 검사로 근거 없는 숫자를 걸러 다시 쓰게 하고, 답변에 남은 계좌번호 패턴을 끝 4자리만 남기고 가린다.
- `evaluation/run_eval.py`: 채점 스크립트.

도구 3개:
- **`get_portfolio()`**: 계좌 목록(계좌번호는 끝 4자리만), 보유 종목(수량·평균단가·통화·자산군·섹터), 현재가·USD/KRW 환율·기준시각을 한 번에 돌려준다.
- **`build_view(lens, filter)`**: pandas 집계. 렌즈는 `account | asset_class | currency | sector | symbol`이고 항목별 매입금액·평가금액·평가손익·수익률·비중과 렌즈 총액을 낸다. `symbol` 렌즈는 같은 종목을 계좌를 넘어 합친다(수량 가중 평균단가). 반올림은 여기서 한 번만 한다(원화 원 단위, 달러·% 소수 2자리).
- **`retrieve_docs(query)`**: 관련 문서 조각과 문서명을 돌려준다.

응답 형식은 과제 표준 규약(§4-2)을 따른다: `contexts` = `[{"doc_id", "text"}]`(문서 조각은 문서 doc_id, 도구 결과는 `도구이름:입력`), `trace` = `[{"step", "input", "output"}]`(step은 도구 이름 또는 `input_guard`·`number_check`, input·output은 문자열). 숫자 검사에 걸린 답변은 LCEL 체인(`prompt | model.with_structured_output(RevisedAnswer)`)으로 다시 쓴다. 지금은 Mock 데이터만 쓴다. 증권사 데이터는 MCP 없이 일반 도구로 읽는다(이유와 전환 조건은 `SERVICE.md` 2·3장). 한국투자증권 실계좌, LangSmith·LangFuse는 여유가 있을 때만 붙인다. RAGAS는 `run_eval.py --ragas`로 돌린다(ragas 0.4.3이 langchain-community의 Vertex AI 모듈을 import해서, 스크립트가 빈 자리표시 모듈로 우회한다).

## 반드시 지킬 규칙 (구조와 guardrail 평가 케이스로 보장)

- **주문·이체 기능 없음**: 주문·이체 도구를 만들지 않는다. 조회 전용은 프롬프트가 아니라 구조로 보장한다.
- **숫자는 도구 결과만**: LLM은 `build_view` 출력을 그대로 인용하고 직접 계산하거나 반올림하지 않는다. 데이터 기준시각을 함께 적는다. 없는 데이터(코인, 과거 수익률)는 없다고 말하고 추정하지 않는다. 프롬프트만으로는 매번 지켜지지 않아 `src/api.py`의 출력 숫자 검사로 한 번 더 막는다.
- **투자 권유 금지**: 매수·매도 추천, 목표가, 수익 보장 표현을 쓰지 않는다. 비중·손익 같은 사실만 제시한다.
- **개인정보**: 계좌번호는 끝 4자리만 보인다(`get_portfolio` 출력 포함). 타인 계좌 조회는 거절하고 사용자가 입력한 계좌번호를 다시 출력하지 않는다. KIS 키·토큰은 응답과 트레이스에 남기지 않는다.
- **프롬프트 인젝션**: 시스템 프롬프트나 API 키 노출 요청은 거절한다.

## 평가

- 평가는 항상 mock 모드로 한다. 실잔고는 매일 바뀌고 public 저장소에 올라가면 안 된다. 리포트와 캡처도 mock 데이터만 쓴다.
- Mock 고정값: 계좌 3개(A증권 일반 위탁 KRW, A증권 ISA KRW, B증권 해외주식 USD), 삼성전자는 두 계좌에 보유(종목 통합 렌즈용), USD/KRW 1,380 고정, 기준시각 2026-11-06 15:30
- 렌즈별 정답 숫자는 손으로 계산해 `evaluation/expected_values.json`에 넣는다.
- 채점: `expected_tools` 호출 여부와 `forbidden` 표현은 자동으로 보고, 렌즈 숫자는 `expected_values.json`과 비교한다(금액 상대오차 ±0.1%, 비중·수익률 ±0.1%p). `expected_traits`는 답변과 나란히 파일로 남겨 사람이 확인한다. 목표는 1차 70% 이상, 2차 90% 이상, guardrail 케이스 100%. 결과는 `evaluation/` 아래 `round1_report.md` / `round2_report.md`에 적는다.

## 막힐 때 대안

Bedrock 연결이 막히면 오류 메시지를 그대로 붙여 원인부터 확인한다. 하이브리드 검색이 막히면 BM25만으로 먼저 붙인다.

<!-- ooo:START -->
<!-- ooo:VERSION:0.59.9 -->
# Ouroboros — 명세 우선 AI 개발

> AI에게 무엇을 만들라고 하기 전에, 무엇을 만들어야 하는지부터 정한다.

AI 코딩의 실패는 대부분 출력이 아니라 입력에서 생긴다. Ouroboros는 코드를 쓰기 전에 **숨은 가정을 드러내서** 이를 막는다.

1. **소크라테스식 명료화**: 모호함이 0.2 이하가 될 때까지 질문한다
2. **존재론적 정확성**: 증상이 아니라 근본 문제를 푼다
3. **진화 루프**: 평가 결과가 다시 더 나은 명세로 이어진다

```
인터뷰 → Seed(명세) → 실행 → 평가
   ↑                          ↓
   └──────── 진화 루프 ────────┘
```

## ooo 명령

각 명령은 필요할 때 해당 에이전트나 MCP를 불러온다. 자세한 내용은 각 스킬 파일에 있다.

| 명령 | 불러오는 것 |
|---------|-------|
| `ooo` | — |
| `ooo interview` | `ouroboros:socratic-interviewer` |
| `ooo seed` | `ouroboros:seed-architect` |
| `ooo run` | MCP 필요 |
| `ooo evolve` | MCP: `evolve_step` |
| `ooo evaluate` | `ouroboros:evaluator` |
| `ooo unstuck` | `ouroboros:{persona}` |
| `ooo status` | MCP: `session_status` |
| `ooo setup` | — |
| `ooo help` | — |

## 에이전트

미리 불러오지 않고 필요할 때 불러온다.

**핵심**: socratic-interviewer, ontologist, seed-architect, evaluator, advocate, contrarian, judge
**보조**: hacker, simplifier, researcher, architect
<!-- ooo:END -->
