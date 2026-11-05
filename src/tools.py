"""PortfolioLens 도메인 도구: 포트폴리오 조회(get_portfolio)와 렌즈별 집계(build_view)."""

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from langchain_core.tools import tool

BROKER_DIR = Path(__file__).resolve().parent.parent / "data" / "broker"
ACCOUNT_FILES = ["a_general.json", "a_isa.json", "b_overseas.json"]

# 렌즈 이름 → 묶는 기준 열
LENS_KEYS = {
    "account": "account_id",
    "asset_class": "asset_class",
    "currency": "currency",
    "sector": "sector",
    "symbol": "symbol",
}
LensName = Literal["account", "asset_class", "currency", "sector", "symbol"]


def _mask_account_no(account_no: str) -> str:
    """계좌번호를 끝 4자리만 남기고 가린다."""
    digits = account_no.replace("-", "")
    return "****" + digits[-4:]


def _round_won(value: float) -> int:
    """원화 금액을 원 단위로 반올림한다(사사오입)."""
    return int(Decimal(str(value)).quantize(Decimal("1"), ROUND_HALF_UP))


def _round2(value: float) -> float:
    """달러 금액이나 퍼센트를 소수 둘째 자리로 반올림한다(사사오입)."""
    return float(Decimal(str(value)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def _read_json(name: str) -> dict:
    """data/broker 아래 JSON 파일 하나를 읽는다."""
    return json.loads((BROKER_DIR / name).read_text(encoding="utf-8"))


def load_portfolio() -> dict:
    """Mock 계좌 3개와 시세를 읽어 계좌 목록, 보유 내역, 시세를 한 덩어리로 돌려준다.

    계좌번호는 이 단계에서 이미 끝 4자리만 남긴다. 현금도 보유 내역 한 줄로 넣는다.
    """
    quotes = _read_json("quotes.json")
    accounts, positions = [], []
    for file_name in ACCOUNT_FILES:
        acct = _read_json(file_name)
        accounts.append({
            "account_id": acct["account_id"],
            "broker": acct["broker"],
            "account_type": acct["account_type"],
            "alias": acct["alias"],
            "account_no_masked": _mask_account_no(acct["account_no"]),
            "base_currency": acct["base_currency"],
        })
        common = {"account_id": acct["account_id"], "broker": acct["broker"], "account_type": acct["account_type"]}
        for h in acct["holdings"]:
            positions.append({**common, **h, "current_price": quotes["prices"][h["symbol"]]})
        for c in acct["cash"]:
            positions.append({
                **common,
                "symbol": f"CASH_{c['currency']}",
                "name": "원화 현금" if c["currency"] == "KRW" else "달러 현금",
                "quantity": c["amount"],
                "avg_price": 1,
                "current_price": 1,
                "currency": c["currency"],
                "asset_class": "현금",
                "sector": "현금",
                "country": "KR" if c["currency"] == "KRW" else "US",
            })
    return {
        "as_of": quotes["as_of"],
        "fx": {"USDKRW": quotes["fx"]["USDKRW"]},
        "accounts": accounts,
        "positions": positions,
    }


def _positions_frame(portfolio: dict) -> pd.DataFrame:
    """보유 내역을 표로 바꾸고 원화 환산 매입금액·평가금액을 붙인다.

    달러 자산은 매입금액도 현재 환율로 환산한다(환차손익은 다루지 않는다).
    """
    df = pd.DataFrame(portfolio["positions"])
    rate = df["currency"].map({"KRW": 1, "USD": portfolio["fx"]["USDKRW"]})
    df["cost_native"] = df["quantity"] * df["avg_price"]
    df["value_native"] = df["quantity"] * df["current_price"]
    df["cost_krw"] = df["cost_native"] * rate
    df["market_value_krw"] = df["value_native"] * rate
    return df


def _summarize(cost: float, value: float, total_value: float) -> dict:
    """매입금액·평가금액으로 손익, 수익률, 비중을 계산하고 반올림한다."""
    pnl = value - cost
    return {
        "cost_krw": _round_won(cost),
        "market_value_krw": _round_won(value),
        "unrealized_pnl_krw": _round_won(pnl),
        "return_pct": _round2(pnl / cost * 100) if cost else 0.0,
        "weight_pct": _round2(value / total_value * 100) if total_value else 0.0,
    }


def compute_view(
    lens: str,
    account_id: Optional[str] = None,
    broker: Optional[str] = None,
    account_type: Optional[str] = None,
) -> dict:
    """렌즈 하나로 보유 자산을 묶어 항목별 금액·손익·비중과 렌즈 총액을 계산한다.

    필터(계좌, 증권사, 계좌유형)를 주면 그 범위만 집계하고, 비중도 그 범위 안에서 계산한다.
    """
    if lens not in LENS_KEYS:
        return {"error": f"지원하지 않는 렌즈입니다: {lens}. 가능한 렌즈: {', '.join(LENS_KEYS)}"}

    portfolio = load_portfolio()
    df = _positions_frame(portfolio)
    filters = {"account_id": account_id, "broker": broker, "account_type": account_type}
    for col, val in filters.items():
        if val:
            df = df[df[col] == val]
    applied = {k: v for k, v in filters.items() if v}
    if df.empty:
        return {"error": "조건에 맞는 보유 자산이 없습니다.", "filter": applied,
                "available_accounts": portfolio["accounts"]}

    key = LENS_KEYS[lens]
    total_value = df["market_value_krw"].sum()
    total_cost = df["cost_krw"].sum()
    labels = {a["account_id"]: a for a in portfolio["accounts"]}

    rows = []
    for group_key, g in df.groupby(key, sort=False):
        row = {"key": group_key, **_summarize(g["cost_krw"].sum(), g["market_value_krw"].sum(), total_value)}
        if lens == "account":
            acct = labels[group_key]
            row.update(alias=acct["alias"], broker=acct["broker"], account_no_masked=acct["account_no_masked"])
        if lens == "symbol":
            row["name"] = g["name"].iloc[0]
            row["quantity"] = g["quantity"].sum()
            if not str(group_key).startswith("CASH_"):
                row["currency"] = g["currency"].iloc[0]
                row["avg_price"] = _round2(g["cost_native"].sum() / g["quantity"].sum())
                row["accounts"] = sorted(g["account_id"].unique().tolist())
        if lens == "currency" and group_key == "USD":
            row["market_value_usd"] = _round2(g["value_native"].sum())
        rows.append(row)
    rows.sort(key=lambda r: r["market_value_krw"], reverse=True)

    result = {
        "lens": lens,
        "filter": applied,
        "as_of": portfolio["as_of"],
        "fx_usdkrw": portfolio["fx"]["USDKRW"],
        "rows": rows,
        "total": {k: v for k, v in _summarize(total_cost, total_value, total_value).items() if k != "weight_pct"},
    }
    if lens == "account":
        result["broker_subtotals"] = [
            {"broker": b, **_summarize(g["cost_krw"].sum(), g["market_value_krw"].sum(), total_value)}
            for b, g in df.groupby("broker", sort=False)
        ]
    return result


@tool
def get_portfolio() -> dict:
    """연결된 증권 계좌 목록과 보유 종목, 현재가, 환율, 데이터 기준시각을 한 번에 조회한다.

    이럴 때 쓴다:
    - 어떤 계좌·증권사가 연결되어 있는지 확인할 때 ("내 계좌 뭐 있어?", "코인도 보여줘" → 연결된 범위 안내)
    - 특정 종목의 보유 수량·평균단가를 그대로 볼 때
    계좌번호는 끝 4자리만 들어 있다. 금액 합계·비중·손익은 계산하지 말고 build_view를 쓴다.
    """
    portfolio = load_portfolio()
    return {
        "as_of": portfolio["as_of"],
        "fx": portfolio["fx"],
        "accounts": portfolio["accounts"],
        "positions": [
            {k: p[k] for k in ("account_id", "symbol", "name", "quantity", "avg_price", "current_price",
                               "currency", "asset_class", "sector")}
            for p in portfolio["positions"]
        ],
        "connected_scope": "국내·해외 주식, ETF, 예수금(현금)만 연결되어 있다. 코인, 부동산, 과거 시점 평가 데이터는 없다.",
    }


@tool
def build_view(
    lens: LensName,
    account_id: Optional[str] = None,
    broker: Optional[str] = None,
    account_type: Optional[str] = None,
) -> dict:
    """보유 자산을 원하는 관점(렌즈)으로 묶어 원화 기준 매입금액·평가금액·평가손익·수익률·비중을 계산한다.

    금액·비중·손익 숫자는 반드시 이 도구 결과를 그대로 인용한다(직접 계산하거나 반올림하지 않는다).

    lens 고르는 법:
    - account: 계좌별 ("계좌별로 나눠서 보여줘", "전체 자산 얼마야?" → total과 broker_subtotals 사용)
    - asset_class: 자산군별 국내주식/해외주식/ETF/현금 ("자산군별 비중은?")
    - currency: 통화별 KRW/USD, USD 행에 달러 원금액(market_value_usd) 포함 ("달러 자산 얼마야?")
    - sector: 섹터별 ("섹터별로 보여줘")
    - symbol: 종목 통합, 여러 계좌의 같은 종목을 합쳐 합산 수량과 수량 가중 평균단가 제공 ("삼성전자 합치면 평단?")
    "제일 많이 쏠린 게 뭐야?"처럼 기준이 애매하면 symbol과 sector를 둘 다 불러 두 관점을 구분해 답한다.

    필터(선택): account_id(예: a-general, a-isa, b-overseas), broker(예: A증권, B증권),
    account_type(예: 일반 위탁, ISA, 해외주식). 필터를 주면 비중도 그 범위 안에서 계산한다.
    """
    return compute_view(lens, account_id=account_id, broker=broker, account_type=account_type)


TOOLS = [get_portfolio, build_view]


if __name__ == "__main__":
    print(json.dumps(compute_view("account"), ensure_ascii=False, indent=2))
