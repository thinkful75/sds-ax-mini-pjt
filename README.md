# 미니 PJT: PortfolioLens — 내 증권 자산 멀티뷰 에이전트

## 무엇을 푸나
여러 증권사에 흩어진 내 증권 자산을 질문 한 줄로 계좌별·자산군별·통화별·섹터별·종목 통합 관점으로 바꿔 보여준다.

## 활용한 패턴 (Day 1~7)
- Day 1: LCEL chain (Pydantic 구조화 출력) — 숫자 검사에 걸린 답변을 `ChatPromptTemplate | model.with_structured_output(RevisedAnswer)` 체인으로 다시 쓰고(`src/agent.py`), API 응답은 Pydantic `QueryResponse{answer, contexts[doc_id,text], trace[step,input,output]}`로 반환(`src/api.py`)
- Day 2: RAG (하이브리드 검색) — Chroma + Titan 임베딩과 BM25를 RRF로 합친 `retrieve_docs`. 임베딩이 막히면 BM25만으로 동작
- Day 3: ReAct (도구 자율 선택) — `create_agent` + Bedrock `ChatBedrockConverse`(Claude Sonnet 4.6)가 질문에 맞는 렌즈·도구를 스스로 고름
- Day 4: 도구 다중 — `get_portfolio`, `build_view`, `retrieve_docs`를 한 질의에서 결합(예: 양도세 질문 = `retrieve_docs` + `build_view`, "원화로 하면 얼마야? 그리고 통화별로도" = `build_view` 2회)
- Day 5: 가드레일 (입력·출력 필터링) — 입력: 프롬프트 인젝션은 모델 호출 전에 차단(`input_guard`). 출력: 도구·문서에 없는 숫자를 걸러 다시 쓰기(`number_check`), 계좌번호 마스킹. 구조: 주문·이체 도구 없음
- Day 5: 미들웨어 (마스킹·재시도) — `create_agent`에 `PIIMiddleware`(사용자가 입력한 계좌번호를 모델에 넘기기 전에 가리고 답변에서도 가림)와 `ModelRetryMiddleware`(Bedrock 일시 오류 재시도)
- Day 7: 평가 (RAGAS) — 자체 16문항 채점(도구 호출 · 금지 표현 · 렌즈 숫자 정답 비교)과 문서 질문 6개의 RAGAS 4개 지표

## 아키텍처
```
POST /query {question}  (또는 브라우저 채팅 화면 GET /)
  → src/api.py     입력 가드레일: 프롬프트 인젝션이면 모델 호출 없이 거절 (trace: input_guard)
  → src/agent.py   create_agent 에이전트 (시스템 프롬프트 = 답하는 방법 + 가드레일 규칙)
      미들웨어: PIIMiddleware(입력·출력 계좌번호 가림) · ModelRetryMiddleware(일시 오류 재시도)
      ├─ get_portfolio()          src/tools.py      ← data/broker/*.json (Mock)
      ├─ build_view(lens, 필터)    src/tools.py      숫자 계산·반올림은 전부 여기 (pandas)
      └─ retrieve_docs(query)     src/retriever.py  ← data/docs/*.md (Chroma + BM25 하이브리드)
  → src/api.py     출력 가드레일 ① 숫자 검사: 도구·문서에 없는 숫자 → LCEL 체인으로 한 번 다시 쓰기 (trace: number_check)
                               ② 계좌번호 마스킹(끝 4자리만)
  → {"answer": "...", "contexts": [{"doc_id", "text"}], "trace": [{"step", "input", "output"}]}
```
렌즈 5개: 계좌별(`account`) · 자산군별(`asset_class`) · 통화별(`currency`) · 섹터별(`sector`) · 종목 통합(`symbol`, 여러 계좌의 같은 종목을 수량 가중 평단으로 합침)

## 실행 방법
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # macOS/Linux는 .venv/bin/python
cp .env.example .env          # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, BEDROCK_MODEL_ID, JUDGE_MODEL_ID 입력
.venv/Scripts/python -m uvicorn src.api:app --port 8000    # http://localhost:8000/ 채팅 화면
curl -X POST localhost:8000/query -H "Content-Type: application/json" -d '{"question": "계좌별로 나눠서 보여줘"}'
.venv/Scripts/python src/agent.py "삼성전자 합치면 평단 얼마야?"   # 질문 하나를 바로 실행
.venv/Scripts/python evaluation/run_eval.py --round 2       # 16문항 평가 (Mock 데이터)
.venv/Scripts/python evaluation/run_eval.py --ragas         # 문서 질문 6개 RAGAS 지표
```
- Bedrock 계정에서 Claude 모델과 `amazon.titan-embed-text-v2:0` 호출 권한이 필요하다.
- 데이터는 Mock(계좌 3개, USD/KRW 1,380 고정, 기준시각 2026-11-06 15:30)이다. 실계좌 정보는 저장소에 없다.

## RAGAS 평가 결과
- context_recall: 0.92
- context_precision: 0.60
- faithfulness: 0.75
- answer_relevancy: 0.38

문서 질문 6개(평가셋 6·9·12번 + 추가 3개)의 평균이다. ragas 0.4.3, 채점 모델 Claude Haiku 4.5(답변 모델과 분리), 임베딩 Titan v2. 정답 문장은 `evaluation/expected_values.json`의 `ragas`, 문항별 점수는 `evaluation/ragas_results.json`.
- **검색은 잘 됨**: 필요한 문서 조각은 대부분 가져온다(recall 0.92). 다만 다른 문서 조각도 함께 올라와 precision이 낮다.
- **answer_relevancy가 낮은 이유**: 9번(연금저축)은 설계대로 "문서에 없는 내용"이라고 답해 0점이고, 나머지도 기준시각·출처·주의사항을 붙인 긴 한국어 답변이라 답변에서 거꾸로 만든 질문과 원래 질문의 유사도가 낮게 나온다.
- **faithfulness가 목표(0.8)에 못 미친 이유**: 9번은 문서에 없는 공식 채널(홈택스 등)을 안내해 0.3, 배당 질문(r1)은 문서 밖 일반 설명이 섞여 0.54였다. 개선 후보는 2차 보고서 7장.

## 인-아웃 세트 통과율 (자체 평가)
- 1차 (Day 9 종료): 14 / 16 통과 (`evaluation/round1_report.md`)
- 2차 (Day 10 개선 후): 16 / 16 통과, guardrail 3 / 3 (`evaluation/round2_report.md`). 입력 가드레일·미들웨어·표준 응답 형식을 붙인 최종 코드로 다시 돌려도 16 / 16
- 개선폭: +2 건
  - 14번 "엔비디아 더 사야 할까?": 권유를 거절만 하고 사실 정보가 없었음 → 거절 후 보유 수량·손익·비중을 조회해 제시
  - 12번 "미국 주식 양도세 내야 하는 수준이야?": 종목별 손익만 나열 → 합계 렌즈로 4,554,000원을 조회해 기본공제 250만 원과 비교
  - 7번은 채점 기준을 고침(범위 밖 안내에 도구 호출을 요구한 기준이 과했음, 2차 보고서 3장)

## 트라이앤에러 회고
- **시도했지만 실패한 접근**: "숫자는 직접 계산하지 말라"를 시스템 프롬프트에만 넣었다. 단독 실행에서는 지켜졌지만 16문항 전체 실행에서 12번이 과세표준(4,554,000 − 2,500,000)을 계속 직접 계산했다. 자동 채점은 "정답 숫자가 답에 있는가"만 봐서 이걸 잡지 못했다.
- **최종 채택한 접근**: 출력 숫자 검사를 API에 붙였다. 답변의 숫자가 질문·도구 결과·문서에 없으면 그 숫자를 빼고 한 번 다시 쓰게 한다. 규칙을 프롬프트가 아니라 구조로 보장하는 방식이라, 주문 도구를 아예 만들지 않은 것과 같은 원칙이다.
- **MCP를 쓰지 않은 이유**: 에이전트가 하나뿐이라 MCP의 이득(여러 클라이언트 공유, 키 격리)이 작고, 별도 프로세스와 디버깅 비용은 크다. 실계좌를 붙여도 `load_portfolio()`의 데이터 출처만 바꾸면 된다.
- **남은 한계 · 향후 개선 방향**
  - 숫자 검사는 "같은 숫자가 근거에 있는가"만 본다. 직접 계산한 값이 우연히 도구 숫자와 같으면 통과한다.
  - 금지 항목이 설명형 문장이라 자동 채점으로는 거의 안 잡힌다. LLM-as-Judge로 보강할 수 있다.
  - RAGAS faithfulness 0.75: 문서에 없는 안내(공식 채널 등)를 덧붙이는 습관. 시스템 프롬프트에 "문서 질문은 문서 내용만" 규칙을 더하는 것이 다음 개선 후보.
  - ragas 0.4.3이 최신 langchain-community와 맞지 않아(Vertex AI 모듈 import), 채점 스크립트에서 빈 자리표시 모듈로 우회했다.
  - 한국투자증권 실계좌 연결, 거래내역 기반 실현손익 렌즈, LangSmith·LangFuse 트레이스 연결

## 핵심 코드 위치
- `src/agent.py:90` — 메인 에이전트 그래프 `get_agent()` (create_agent + 도구 3개 + 미들웨어)
  - `src/agent.py:29` 시스템 프롬프트 · `:76` 미들웨어(PII·재시도) · `:110` LCEL 다시 쓰기 체인 · `:138` `run_query()`(contexts·trace 생성)
- `src/tools.py` — 도메인 도구: `:177` `get_portfolio`, `:200` `build_view`, `:113` 렌즈 집계 `compute_view`
- `src/retriever.py` — RAG 파이프라인: `:61` 하이브리드 검색 `HybridRetriever`, `:104` `retrieve_docs`
- `src/api.py` — `:155` `POST /query`, `:126` 입력·출력 가드레일 흐름 `answer_question()`, `:105` 숫자 검사, `:201` 채팅 화면
- `evaluation/run_eval.py` — `:85` 16문항 채점, `:135` RAGAS. 정답 숫자는 `evaluation/expected_values.json`
