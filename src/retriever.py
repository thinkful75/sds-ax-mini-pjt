"""PortfolioLens RAG 파이프라인: data/docs/*.md 문서를 Chroma + BM25 하이브리드로 찾고 LLM으로 리랭킹한다(retrieve_docs)."""

import os
import re
from functools import lru_cache
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

DOCS_DIR = Path(__file__).resolve().parent.parent / "data" / "docs"
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
RERANK_MODEL_ID_DEFAULT = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
TOP_K = 3          # 최종으로 돌려줄 조각 수(최대)
CANDIDATE_K = 8    # 리랭킹 전에 하이브리드 검색으로 뽑는 후보 수
RRF_K = 60  # 순위 결합(Reciprocal Rank Fusion) 상수


class RerankResult(BaseModel):
    """리랭킹 결과: 질문에 답하는 데 필요한 후보 번호(중요한 순)."""

    selected: list[int] = Field(description="질문에 답하는 데 필요한 후보 번호. 중요한 순서. 필요한 것이 없으면 빈 목록")


RERANK_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "너는 검색 결과 리랭커다. 후보 문서 조각 중 질문에 답하는 데 실제로 필요한 것만 골라 중요한 순서로 번호를 적는다. "
               "최대 {k}개. 질문과 상관없는 조각은 넣지 않는다."),
    ("human", "질문: {query}\n\n후보:\n{candidates}"),
])


@lru_cache(maxsize=1)
def get_rerank_chain():
    """LCEL 리랭킹 체인(프롬프트 | Haiku 4.5 구조화 출력)을 한 번만 만든다."""
    from langchain_aws import ChatBedrockConverse

    model = ChatBedrockConverse(model_id=os.environ.get("RERANK_MODEL_ID", RERANK_MODEL_ID_DEFAULT),
                                region_name=os.environ.get("AWS_REGION"), temperature=0, max_tokens=256)
    return RERANK_PROMPT | model.with_structured_output(RerankResult)


def rerank(query: str, docs: list[Document], k: int = TOP_K) -> list[Document]:
    """후보 조각을 LLM으로 다시 골라 최대 k개를 돌려준다. 실패하면 원래 순서의 앞 k개를 돌려준다."""
    if len(docs) <= 1:
        return docs[:k]
    candidates = "\n\n".join(f"[{i}] {d.page_content[:600]}" for i, d in enumerate(docs))
    try:
        picked = get_rerank_chain().invoke({"query": query, "candidates": candidates, "k": k}).selected
        chosen = [docs[i] for i in dict.fromkeys(picked) if 0 <= i < len(docs)][:k]
        return chosen or docs[:1]  # 하나도 안 고르면 1위 하나만 남겨 "문서에 없음" 판단에 쓰게 한다
    except Exception as exc:  # 리랭킹이 안 되면 하이브리드 순서를 그대로 쓴다
        print(f"[retriever] 리랭킹 실패, 하이브리드 순서를 씁니다: {type(exc).__name__}")
        return docs[:k]


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """문서 머리의 --- 블록에서 doc_id, title 등을 읽고 본문과 나눈다."""
    meta = {}
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return meta, text
    for line in m.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, text[m.end():]


def load_chunks() -> list[Document]:
    """문서를 '## ' 소제목 단위로 잘라 문서명·소제목을 메타데이터로 붙인다."""
    chunks = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
        title = meta.get("title", path.stem)
        for part in re.split(r"\n(?=## )", body):
            part = part.strip()
            if not part or part.startswith("# ") and "\n" not in part:
                continue
            first = part.splitlines()[0]
            section = first.lstrip("#").strip() if first.startswith("#") else title
            chunks.append(Document(
                page_content=f"[{title}] {part}",
                metadata={"doc_id": meta.get("doc_id", path.stem), "title": title,
                          "source": path.name, "section": section},
            ))
    return chunks


def _tokenize(text: str) -> list[str]:
    """BM25용 토큰화. 한국어는 조사가 붙으므로 단어와 함께 글자 2개씩 묶은 조각도 넣는다."""
    tokens = []
    for word in re.findall(r"[0-9A-Za-z가-힣]+", text.lower()):
        tokens.append(word)
        if re.search(r"[가-힣]", word) and len(word) > 2:
            tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
    return tokens


class HybridRetriever:
    """BM25 키워드 검색과 Chroma 벡터 검색 결과를 순위 결합으로 합친다. 벡터 검색이 안 되면 BM25만 쓴다."""

    def __init__(self, chunks: list[Document]):
        """문서 조각으로 BM25 색인과 (가능하면) Chroma 색인을 만든다."""
        self.chunks = chunks
        self.bm25 = BM25Okapi([_tokenize(c.page_content) for c in chunks])
        self.vector_store = None
        self.mode = "bm25"
        try:
            from langchain_aws import BedrockEmbeddings
            from langchain_chroma import Chroma

            embeddings = BedrockEmbeddings(model_id=EMBED_MODEL_ID, region_name=os.environ.get("AWS_REGION"))
            self.vector_store = Chroma.from_documents(
                chunks, embeddings, ids=[str(i) for i in range(len(chunks))], collection_name="portfoliolens_docs")
            self.mode = "hybrid"
        except Exception as exc:  # 자격 증명·네트워크 문제면 BM25만으로 계속한다
            print(f"[retriever] 벡터 검색을 끄고 BM25만 사용합니다: {type(exc).__name__}")

    def search(self, query: str, k: int = TOP_K) -> list[Document]:
        """질문과 관련된 문서 조각 k개를 돌려준다."""
        scores = self.bm25.get_scores(_tokenize(query))
        bm25_rank = sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True)
        fused = {i: 1 / (RRF_K + r) for r, i in enumerate(bm25_rank[:10]) if scores[i] > 0}
        if self.vector_store is not None:
            try:
                for r, doc in enumerate(self.vector_store.similarity_search(query, k=10)):
                    i = self.chunks.index(next(c for c in self.chunks if c.page_content == doc.page_content))
                    fused[i] = fused.get(i, 0) + 1 / (RRF_K + r)
            except Exception as exc:
                print(f"[retriever] 벡터 검색 실패, BM25 결과만 씁니다: {type(exc).__name__}")
        best = sorted(fused, key=fused.get, reverse=True)[:k]
        return [self.chunks[i] for i in best]


@lru_cache(maxsize=1)
def get_retriever() -> HybridRetriever:
    """검색기를 처음 한 번만 만들고 재사용한다."""
    return HybridRetriever(load_chunks())


@tool
def retrieve_docs(query: str) -> dict:
    """세금·계좌 제도·투자 용어 문서에서 질문과 관련된 부분을 찾는다.

    이럴 때 쓴다: 해외주식 양도소득세, 배당소득세, ISA 제도, 투자 용어(평가손익, 수익률, 비중 등) 질문.
    답변에는 결과의 문서명(title)을 출처로 인용한다. 결과에 답이 없으면 지어내지 말고
    "준비된 문서에 없는 내용"이라고 안내하고 국세청 등 공식 채널 확인을 권한다.
    """
    docs = rerank(query, get_retriever().search(query, k=CANDIDATE_K))
    return {
        "query": query,
        "results": [
            {"doc_id": d.metadata["doc_id"], "title": d.metadata["title"], "section": d.metadata["section"],
             "source": d.metadata["source"], "content": d.page_content}
            for d in docs
        ],
        "available_documents": sorted({c.metadata["title"] for c in get_retriever().chunks}),
    }


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    for q in ["해외주식 양도소득세 기본공제가 얼마야?", "ISA 비과세 한도", "연금저축 세액공제 한도"]:
        hits = retrieve_docs.invoke({"query": q})["results"]
        print(q, "→", [(h["title"], h["section"]) for h in hits])
