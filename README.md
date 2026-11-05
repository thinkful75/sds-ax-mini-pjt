# 미니 PJT: PortfolioLens — 내 증권 자산 멀티뷰 에이전트

## 무엇을 푸나
여러 증권사에 흩어진 내 증권 자산을 질문 한 줄로 계좌별·자산군별·통화별·섹터별·종목 통합 관점으로 바꿔 보여준다.

## 활용한 패턴 (Day 1~7)
- Day 1: LCEL + Pydantic 구조화 출력 — 최종 응답 `{answer, contexts, trace}` 스키마
- Day 2: RAG (하이브리드 검색) — 세금·계좌 제도 문서 `retrieve_docs`
- Day 3: ReAct — 질문에 맞는 렌즈·도구 자율 선택
- Day 4: 도구 다중 + MCP — 한국투자증권 Open API를 감싼 `securities-mcp` 서버(조회 전용, kis/mock 전환) + `build_view`
- Day 5: 가드레일 — 투자 권유·타인 계좌·프롬프트 인젝션 차단, 계좌번호 마스킹
- Day 7: Trace — LangFuse / 평가 — RAGAS + LLM-as-Judge + 숫자 정답 비교

## 아키텍처
`PLAN.md` 0장 참고.
질문 → 입력 가드레일 → ReAct 에이전트(MCP 도구 · build_view · retrieve_docs) → 출력 가드레일 → 구조화 응답 + 트레이스

## 실행 방법
```bash
pip install -r requirements.txt
cp .env.example .env          # OPENAI_API_KEY, LANGFUSE_*, BROKER_PROVIDER 입력
                              # kis 모드면 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO 추가
uvicorn src.api:app --port 8000
curl -X POST localhost:8000/query -H "Content-Type: application/json" -d '{"question": "계좌별로 나눠서 보여줘"}'
BROKER_PROVIDER=mock python evaluation/run_eval.py # 인-아웃 세트 평가 (항상 mock)
```

## RAGAS 평가 결과
- context_recall: 0.XX
- context_precision: 0.XX
- faithfulness: 0.XX
- answer_relevancy: 0.XX

## 인-아웃 세트 통과율 (자체 평가)
- 1차 (Day 9 종료): XX / 16 통과
- 2차 (Day 10 개선 후): XX / 16 통과
- 개선폭: +XX 건 (주요 개선 사항: ...)

## 트라이앤에러 회고
- 시도했지만 실패한 접근 · 왜 실패했는가
- 최종 채택한 접근 · 왜 그것으로 갔는가
- 남은 한계 · 향후 개선 방향 (예: 다른 증권사 Provider 추가, 거래내역 기반 실현손익 렌즈)

## 핵심 코드 위치
- `src/agent.py` — 메인 에이전트 그래프
- `src/mcp_server.py` — 증권사 MCP 서버
- `src/providers/kis.py` · `src/providers/mock.py` — 한국투자증권 API / 가짜 데이터 (같은 출력 형식)
- `src/views.py` — 렌즈별 집계 `build_view`
- `src/retriever.py` — RAG 파이프라인
- `src/guardrails.py` — 입력/출력 가드레일
- `src/api.py` — `POST /query`
