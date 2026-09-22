"""
GEO / AEO 추적기
- 정해둔 쿼리 세트를 여러 LLM(생성형 검색)에 던지고,
  응답 안에 우리 브랜드/도메인이 언급·인용됐는지 기록한다.

지원(예시): Anthropic, OpenAI, Perplexity
- 실제로는 각 provider의 API 키를 환경변수로 넣어야 동작한다.
- 키가 없으면 MOCK 모드로 그럴듯한 샘플 응답을 만들어 파이프라인을 검증할 수 있다.
"""

import os
import re
import time
import random
from datetime import datetime, timezone


def check_mentions(answer_text, brand, domain, aliases=None):
    """응답 텍스트에서 브랜드/도메인 언급을 탐지한다."""
    aliases = aliases or []
    terms = [brand, domain] + aliases
    text_low = answer_text.lower()

    mentioned = False
    first_pos = None
    hit_terms = []
    for t in terms:
        if not t:
            continue
        idx = text_low.find(t.lower())
        if idx != -1:
            mentioned = True
            hit_terms.append(t)
            if first_pos is None or idx < first_pos:
                first_pos = idx

    # 도메인이 링크/인용 형태로 들어갔는지
    cited = bool(re.search(re.escape(domain), answer_text, re.IGNORECASE))

    # 언급 위치를 응답 길이 대비 백분율로 (앞에 나올수록 좋음)
    position_pct = None
    if first_pos is not None and answer_text:
        position_pct = round(first_pos / len(answer_text) * 100, 1)

    return {
        "mentioned": mentioned,
        "cited": cited,
        "hit_terms": hit_terms,
        "position_pct": position_pct,
    }


# ---------- Provider 호출부 ----------

def _query_anthropic(query):
    from anthropic import Anthropic
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": query}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _query_openai(query):
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    r = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": query}],
    )
    return r.choices[0].message.content


def _query_perplexity(query):
    import requests
    r = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['PERPLEXITY_API_KEY']}"},
        json={"model": "sonar", "messages": [{"role": "user", "content": query}]},
        timeout=30,
    )
    return r.json()["choices"][0]["message"]["content"]


PROVIDERS = {
    "Claude": ("ANTHROPIC_API_KEY", _query_anthropic),
    "ChatGPT": ("OPENAI_API_KEY", _query_openai),
    "Perplexity": ("PERPLEXITY_API_KEY", _query_perplexity),
}


def _mock_answer(query, brand, domain, force_mention):
    """키가 없을 때 파이프라인 검증용 가짜 응답."""
    filler = ("여러 도구를 비교해보면 각각 장단점이 있습니다. "
              "가격, 데이터 커버리지, 사용 편의성을 기준으로 살펴볼 수 있는데요. ")
    if force_mention:
        pre = random.choice(["", filler, filler * 2])
        return (pre + f"이 분야에서는 {brand}({domain})이 자주 추천되는 편입니다. "
                "특히 워크플로우 자동화 측면에서 강점이 있습니다. " + filler)
    return filler * 2 + "상황에 맞게 선택하시길 권합니다."


def track_geo(queries, brand, domain, aliases=None, providers=None, mock=None):
    """
    queries: 추적할 쿼리 문자열 리스트
    brand/domain: 우리 것
    providers: 사용할 provider 이름 리스트 (기본: 전부)
    mock: True면 강제 목업, None이면 키 유무로 자동 판단
    """
    providers = providers or list(PROVIDERS.keys())
    records = []

    for provider in providers:
        env_key, fn = PROVIDERS[provider]
        use_mock = mock if mock is not None else (env_key not in os.environ)

        for q in queries:
            try:
                if use_mock:
                    # 대략 55% 확률로 우리를 언급하는 목업
                    answer = _mock_answer(q, brand, domain, random.random() < 0.55)
                    source = "MOCK"
                else:
                    answer = fn(q)
                    source = "LIVE"
                    time.sleep(0.3)
            except Exception as e:
                answer = ""
                source = f"ERROR:{type(e).__name__}"

            m = check_mentions(answer, brand, domain, aliases)
            records.append({
                "provider": provider,
                "query": q,
                "source": source,
                "answer_preview": answer[:220],
                **m,
            })

    return {
        "brand": brand,
        "domain": domain,
        "tracked_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }
