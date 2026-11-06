# PLAN · PortfolioLens 실행 계획 (Day 9~10)

> 원칙: **작게 끝까지.** 오늘 오전에 "질문 → 도구 → 렌즈 집계 → 답변"이 한 번 도는 최소 버전을 먼저 만들고, 그다음 붙인다.
> 기준 자료: 강의 "AI로 개발하기 with Claude Code"(lesson 1163), `CLAUDE.md` 프로젝트 규칙

## 0. 확정한 결정

| 항목 | 결정 | 이유 |
|---|---|---|
| 데이터 연결 | 일반 도구(`src/tools.py`). MCP는 쓰지 않는다(Day 10 확정) | 강의 자료에 MCP 필수 조건 없음. 에이전트가 하나라 MCP의 이득(클라이언트 공유, 키 격리)보다 별도 프로세스·디버깅 비용이 큼. 전환 조건은 `SERVICE.md` 2장 |
| 도구 수 | 3개 (`get_portfolio`, `build_view`, `retrieve_docs`) | 강의 권장 "처음엔 도구 2~3개, 데이터 1~2종" |
| 실계좌 | Mock으로 먼저 전체를 돌리고, 한국투자증권 실계좌는 나중에 데모용으로 | 강의 권장 "더미 데이터로 우선 진행". 평가는 어차피 Mock |
| 파일 구성 | 정해진 3개 파일 + `src/api.py`, `evaluation/run_eval.py`만 추가 | 강의 예시처럼 데이터 읽기는 도구 안에서, 가드레일은 시스템 프롬프트와 도구 설계로 |
| 평가 | 강의 방식 채점 + 렌즈 숫자 정답 비교 + RAGAS(문서 질문 6개, Day 10 추가) | 숫자 오류는 자동으로 잡아야 함. README 템플릿에 RAGAS 지표 칸이 있음 |
| 기술 스택 | Bedrock `ChatBedrockConverse`, `langchain.agents.create_agent` | `CLAUDE.md` 규칙 |

## 1. 아키텍처

```
사용자 ──POST /query {question}──▶ src/api.py (FastAPI)
                                      │
                         src/agent.py  create_agent + ChatBedrockConverse
                         시스템 프롬프트 = 가드레일 규칙
                          (도구 결과만 근거 · 투자 권유 금지 · 타인 계좌 거절 · 프롬프트/키 노출 거절)
                          ├─ get_portfolio()          ◀── data/broker/*.json   (src/tools.py)
                          ├─ build_view(lens, filter) ← 숫자 계산은 전부 여기  (src/tools.py, pandas)
                          └─ retrieve_docs(query)     ◀── data/docs/*.md       (src/retriever.py)
                                      │
                         src/api.py  ① 출력 숫자 검사(도구·문서에 없는 숫자 → 한 번 다시 쓰기, Day 10 추가)
                                     ② 답변 속 계좌번호 패턴 마스킹(끝 4자리만)
                                      │
                         {answer, contexts, trace}
```

- **조회 전용은 구조로 보장한다.** 주문·이체 도구를 만들지 않는다.
- **계좌번호 마스킹은 두 겹이다.** 도구가 처음부터 끝 4자리만 돌려주고, `api.py`가 답변에 남은 계좌번호 패턴을 한 번 더 가린다(케이스 15).
- **`trace`는 이번 요청의 도구 호출 기록이다.** 도구 이름, 입력, 결과를 담고, 출력 숫자 검사가 작동하면 `number_check`가 덧붙는다. LangFuse 연결은 여유 시 붙인다.

## 2. 도구 설계 (`src/tools.py`, `src/retriever.py`)

| 도구 | 입력 → 출력 | 비고 |
|---|---|---|
| `get_portfolio()` | – → 계좌 목록(증권사·계좌유형·별칭·끝 4자리) + 보유 종목(수량·평균단가·통화·자산군·섹터) + 현재가·USD/KRW 환율·기준시각 | 기존 `list_accounts`·`get_holdings`·`get_quotes`를 하나로 합침 |
| `build_view(lens, filter)` | lens: `account` / `asset_class` / `currency` / `sector` / `symbol`, filter: 계좌·증권사 등 → 항목별 매입금액·평가금액·평가손익·수익률·비중 + 렌즈 총액 | `symbol` 렌즈는 합산 수량·수량 가중 평단, `currency` 렌즈는 USD 원금액 포함 |
| `retrieve_docs(query)` | 질문 → 관련 문서 조각 + 문서명 | Chroma + BM25 하이브리드. 답변에 문서명 인용 |

- 도구 docstring은 한국어로 쓰고, 렌즈별 예시 질문을 넣는다("쏠림" → 종목·섹터 두 관점 등).
- 숫자 반올림은 `build_view`에서 한 번만 한다. 원화는 원 단위, 달러·%는 소수 2자리. LLM은 그 값을 그대로 인용한다.

## 3. 파일 구성

```
src/agent.py            에이전트 + 시스템 프롬프트. python src/agent.py 로 시험 질문 실행
src/tools.py            get_portfolio, build_view (data/broker/*.json 읽기 포함)
src/retriever.py        retrieve_docs (RAG)
src/api.py              POST /query → answer, contexts, trace (+ 출력 숫자 검사, 계좌번호 마스킹), GET / 채팅 화면
data/broker/*.json      Mock 계좌 3개 + 시세(quotes.json)
data/docs/*.md          RAG 문서 4개
evaluation/test_queries.csv     인-아웃 세트 16건 (expected_tools 수정 완료)
evaluation/expected_values.json 렌즈별 정답 숫자
evaluation/run_eval.py          채점 스크립트
evaluation/round1_report.md · round2_report.md
```

- Mock JSON에 자산군·섹터를 직접 넣는다. `data/instruments.csv`는 실계좌를 붙일 때 만든다(KIS 잔고에는 섹터가 없음).

## 4. Mock 데이터 설계

| 증권사 | 계좌 | 보유 종목 |
|---|---|---|
| A증권 | 일반 위탁 (KRW) | 삼성전자, SK하이닉스, KODEX 200, 원화 현금 |
| A증권 | ISA (KRW) | 삼성전자, TIGER 미국S&P500, KODEX 고배당 |
| B증권 | 해외주식 (USD) | AAPL, NVDA, SCHD, 달러 현금 |

- 삼성전자를 두 계좌에 넣어 종목 통합 렌즈 케이스를 만든다.
- 시세·환율은 고정값이다. USD/KRW = 1,380, 기준시각 2026-11-06 15:30.
- 계좌번호는 가짜 번호를 쓴다.

## 5. 평가 설계 (`evaluation/run_eval.py`)

| 무엇을 | 방식 |
|---|---|
| 기대 도구를 불렀는가 | `trace`의 도구 이름과 `expected_tools` 비교 (자동) |
| 금지 표현이 들어갔는가 | `forbidden` 항목이 답변에 있으면 실패 (자동) |
| 렌즈 숫자가 맞는가 | `build_view` 출력과 `expected_values.json` 비교. 금액은 상대오차 ±0.1%, 비중·수익률은 ±0.1%p (자동) |
| 기대 특성을 갖췄는가 | 답변과 `expected_traits`를 나란히 파일로 남겨 사람이 확인 |
| RAGAS 충실도 | 여유 시 추가 |

- 카테고리별 통과율과 전체 통과율을 출력하고 결과를 파일로 저장한다.
- 평가는 항상 Mock 모드로 한다.
- 개선 루프는 강의 방식대로 돈다. 실패 하나 고르기 → 원인 진단 → 한 가지만 고치기 → 다시 채점. 3회 반복하면 멈춘다.

## 6. 일정

### Day 9 · 오늘 (산출물: 동작하는 에이전트 · round1_report.md)

| 시간 | 할 일 | 완료 기준 |
|---|---|---|
| 오전 1 | Mock 데이터 `data/broker/*.json` 3개 + `expected_values.json` | 총액·비중 손계산 끝 |
| 오전 2 | `src/tools.py`: `get_portfolio`, `build_view` (렌즈 5개) | 렌즈 숫자가 정답 JSON과 일치 |
| 오전 3 | `src/agent.py`: create_agent + 시스템 프롬프트 + 도구 연결 | `python src/agent.py`로 케이스 1~5 답변 |
| 오후 1 | `src/retriever.py`: Chroma + BM25 → `retrieve_docs` | 케이스 6 문서명 인용 |
| 오후 2 | `src/api.py`: POST /query + 계좌번호 마스킹 | curl로 세 키 응답, 케이스 14~16 거절 |
| 오후 3 | `test_queries.csv` expected_tools 수정 + `run_eval.py` 작성 → 1차 평가 | 통과율 산출 |
| 마무리 | `round1_report.md`: 통과율 · 실패 케이스 · 원인 · 개선 계획 | ✅ |

> **막히면**: Bedrock 연결이 막히면 오류 메시지를 그대로 붙여 원인부터 확인한다. 하이브리드 검색이 막히면 BM25만으로 먼저 붙인다.

### Day 10 · 마감 (산출물: round2_report.md · 제출)

| 시간 | 할 일 | 완료 기준 |
|---|---|---|
| 오전 | 개선 루프(한 번에 하나씩, 3회) → 2차 평가 → `round2_report.md` | ✅ 16/16, guardrail 3/3. 출력 숫자 검사 추가 |
| 오전 | 여유 시: 실계좌 데모 연결, RAGAS, LangFuse | RAGAS 완료(faithfulness 0.75). 실계좌·LangFuse는 하지 않음. MCP 전환은 하지 않기로 결정 |
| 오후 | 과제 규약 맞추기: 패턴 6개 이상(LCEL·Pydantic, 입력 가드레일, 미들웨어 추가로 7개), API 응답을 표준 형식(`contexts[doc_id,text]`, `trace[step,input,output]`)으로 | ✅ 재평가 16/16 |
| 오후 | 시연 캡처(통합 합계 · 렌즈 전환 · 가드레일 거절, Mock 데이터로) · `requirements.txt` · 새 환경에서 실행 확인 | 새 venv로 실행 성공 |
| 마감 | GitHub public push → 제출 (15:00 마감, 초안 기준이므로 확인 필요) | push 완료(https://github.com/thinkful75/sds-ax-mini-pjt), 제출 남음 |

## 7. 다른 문서에 반영할 것

| 문서 | 바꿀 내용 |
|---|---|
| `evaluation/test_queries.csv` | (완료) `expected_tools`에서 예전 도구 이름을 뺐다. 숫자 질문은 `build_view`만 기대한다. 연결 범위 질문(7번)은 처음에 `get_portfolio`를 기대했으나, 도구 없이도 안내할 수 있어 Day 10에 비웠다(`round2_report.md` 3장). `build_view`가 데이터를 직접 읽어 숫자 질문에는 `get_portfolio`가 필요 없기 때문이다 |
| `SERVICE.md` | (완료) 3장을 도구 3개 구성으로 바꾸고 MCP를 쓰지 않은 이유를 적었다. MCP 전환 조건은 2장 확장 관점에 남겼다. 5장 RAGAS를 "여유 시"로 바꿨다. 범위 밖 질문 정책과 Mock 고정값을 채웠다 |
| `CLAUDE.md` | (완료) 아키텍처 섹션의 MCP·Provider 경로를 위 파일 구성으로 바꿨다 |

## 8. 리스크와 대응

| 리스크 | 대응 |
|---|---|
| LLM이 숫자를 직접 계산·반올림해서 틀림 | 시스템 프롬프트에 "숫자는 build_view 결과를 그대로 인용" + 채점에서 정답과 비교. 프롬프트만으로는 막히지 않아(12번 과세표준) `api.py` 출력 숫자 검사를 추가 |
| 렌즈를 잘못 고름 (예: "쏠림" → 계좌별) | 도구 docstring에 렌즈별 예시 질문 |
| 평가셋 문항에만 맞춰 고침 | 루프가 끝나면 문항 밖 질문을 직접 몇 개 던져 본다 |
| 한 번에 여러 개를 고쳐 원인을 모름 | 루프에서 한 번에 하나만 고치고, 바퀴마다 git 커밋 |
| 앱키·계좌번호·실잔고가 public 저장소에 올라감 | `.env`는 `.gitignore`, 평가 리포트와 캡처는 Mock 결과만 |
