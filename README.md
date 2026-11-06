# 미니 PJT: PortfolioLens — 내 증권 자산 멀티뷰 에이전트

## 무엇을 푸나
여러 증권사에 흩어진 내 증권 자산을 질문 한 줄로 계좌별·자산군별·통화별·섹터별·종목 통합 관점으로 바꿔 보여준다.

## 활용한 패턴 (Day 1~7)
- 구조화 출력: Pydantic 응답 모델 `{answer, contexts, trace}` (`src/api.py`)
- RAG (하이브리드 검색): Chroma + Titan 임베딩 + BM25를 RRF로 합침. 임베딩이 막히면 BM25만으로 동작 (`retrieve_docs`)
- 도구 사용 에이전트: `langchain.agents.create_agent` + Bedrock `ChatBedrockConverse`(Claude Sonnet 4.6)가 질문에 맞는 렌즈·도구를 고름
- 도구 다중: `get_portfolio`, `build_view`, `retrieve_docs` 3개. 증권사 데이터는 MCP 없이 일반 도구로 읽음(이유는 `SERVICE.md` 3장)
- 가드레일: 주문 도구 없음(구조), 시스템 프롬프트 규칙(투자 권유·타인 계좌·인젝션 거절), 출력 숫자 검사, 계좌번호 마스킹
- 평가: 16문항 자동 채점(도구 호출 · 금지 표현 · 렌즈 숫자 정답 비교) + 기대 특성 사람 확인

## 아키텍처
```
POST /query {question}  (또는 브라우저 채팅 화면 GET /)
  → src/agent.py   에이전트 (시스템 프롬프트 = 답하는 방법 + 가드레일 규칙)
      ├─ get_portfolio()          src/tools.py      ← data/broker/*.json (Mock)
      ├─ build_view(lens, 필터)    src/tools.py      숫자 계산·반올림은 전부 여기 (pandas)
      └─ retrieve_docs(query)     src/retriever.py  ← data/docs/*.md
  → src/api.py     ① 출력 숫자 검사: 도구·문서에 없는 숫자가 있으면 그 숫자를 빼고 한 번 다시 쓰게 함
                   ② 계좌번호 마스킹(끝 4자리만)
  → {answer, contexts, trace}
```
렌즈 5개: 계좌별(`account`) · 자산군별(`asset_class`) · 통화별(`currency`) · 섹터별(`sector`) · 종목 통합(`symbol`, 여러 계좌의 같은 종목을 수량 가중 평단으로 합침)

## 실행 방법
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # macOS/Linux는 .venv/bin/python
cp .env.example .env          # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, BEDROCK_MODEL_ID 입력
.venv/Scripts/python -m uvicorn src.api:app --port 8000    # http://localhost:8000/ 채팅 화면
curl -X POST localhost:8000/query -H "Content-Type: application/json" -d '{"question": "계좌별로 나눠서 보여줘"}'
.venv/Scripts/python src/agent.py "삼성전자 합치면 평단 얼마야?"   # 질문 하나를 바로 실행
.venv/Scripts/python evaluation/run_eval.py --round 2       # 16문항 평가 (Mock 데이터)
```
- Bedrock 계정에서 Claude 모델과 `amazon.titan-embed-text-v2:0` 호출 권한이 필요하다.
- 데이터는 Mock(계좌 3개, USD/KRW 1,380 고정, 기준시각 2026-11-06 15:30)이다. 실계좌 정보는 저장소에 없다.

## RAGAS 평가 결과
- 실시하지 않았다. 일정상 "여유 시" 항목으로 두고, 대신 문서 검색 문항(6·9·12번)은 문서명 인용과 "문서에 없는 내용" 안내를 자동 채점·사람 확인으로 봤다.

## 인-아웃 세트 통과율 (자체 평가)
- 1차 (Day 9 종료): 14 / 16 통과 (`evaluation/round1_report.md`)
- 2차 (Day 10 개선 후): 16 / 16 통과, guardrail 3 / 3 (`evaluation/round2_report.md`)
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
  - 금지 항목이 설명형 문장이라 자동 채점으로는 거의 안 잡힌다. LLM 채점(Haiku 4.5)으로 보강할 수 있다.
  - 한국투자증권 실계좌 연결, 거래내역 기반 실현손익 렌즈, RAGAS·LangFuse 연결

## 핵심 코드 위치
- `src/agent.py` — 에이전트와 시스템 프롬프트, 숫자 검사에 걸린 답변을 다시 쓰는 `revise_answer`
- `src/tools.py` — `get_portfolio`, 렌즈별 집계 `build_view` (Mock JSON 읽기 포함)
- `src/retriever.py` — RAG 파이프라인 `retrieve_docs`
- `src/api.py` — `POST /query`, 채팅 화면, 출력 숫자 검사, 계좌번호 마스킹
- `evaluation/run_eval.py` — 채점 스크립트, `evaluation/expected_values.json` — 손으로 계산한 정답 숫자
