"""
내 사이트 / 경쟁사 / 프롬프트 저장소 (Supabase REST 경유).
history_store.py와 같은 이유로 Supabase 바깥 저장이 필요하다 — Render는 재배포마다
로컬 파일시스템이 초기화된다. 읽기는 실패해도 조용히 빈 값(설정 없음)을 반환하지만,
쓰기는 화면에 성공/실패를 보여줘야 하니 True/False를 명시적으로 반환한다.
"""

import requests

SITE_CONFIG_TABLE = "geo_site_config"
COMPETITORS_TABLE = "geo_competitors"
PROMPTS_TABLE = "geo_prompts"


def _headers(api_key):
    return {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def configured(supabase_url, supabase_key):
    return bool(supabase_url and supabase_key)


# ---------------- 내 사이트 ----------------

def get_site_config(supabase_url, supabase_key):
    """{"site_urls": [...], "brand_aliases": [...]} — 없거나 실패하면 빈 리스트로."""
    empty = {"site_urls": [], "brand_aliases": []}
    if not configured(supabase_url, supabase_key):
        return empty
    try:
        resp = requests.get(
            f"{supabase_url}/rest/v1/{SITE_CONFIG_TABLE}",
            params={"id": "eq.1", "select": "site_urls,brand_aliases"},
            headers=_headers(supabase_key),
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return empty
        row = rows[0]
        return {
            "site_urls": row.get("site_urls") or [],
            "brand_aliases": row.get("brand_aliases") or [],
        }
    except Exception:
        return empty


def save_site_config(supabase_url, supabase_key, site_urls, brand_aliases):
    if not configured(supabase_url, supabase_key):
        return False
    try:
        resp = requests.post(
            f"{supabase_url}/rest/v1/{SITE_CONFIG_TABLE}",
            params={"on_conflict": "id"},
            headers={**_headers(supabase_key), "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"id": 1, "site_urls": site_urls, "brand_aliases": brand_aliases},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


# ---------------- 경쟁사 ----------------

def list_competitors(supabase_url, supabase_key):
    if not configured(supabase_url, supabase_key):
        return []
    try:
        resp = requests.get(
            f"{supabase_url}/rest/v1/{COMPETITORS_TABLE}",
            params={"select": "id,name,domain,aliases", "order": "created_at.asc"},
            headers=_headers(supabase_key),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def add_competitor(supabase_url, supabase_key, name, domain, aliases):
    if not configured(supabase_url, supabase_key):
        return False
    try:
        resp = requests.post(
            f"{supabase_url}/rest/v1/{COMPETITORS_TABLE}",
            headers={**_headers(supabase_key), "Prefer": "return=minimal"},
            json={"name": name, "domain": domain, "aliases": aliases},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def delete_competitor(supabase_url, supabase_key, competitor_id):
    if not configured(supabase_url, supabase_key):
        return False
    try:
        resp = requests.delete(
            f"{supabase_url}/rest/v1/{COMPETITORS_TABLE}",
            params={"id": f"eq.{competitor_id}"},
            headers=_headers(supabase_key),
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


# ---------------- 프롬프트 ----------------

def list_prompts(supabase_url, supabase_key, include_archived=True):
    if not configured(supabase_url, supabase_key):
        return []
    try:
        params = {"select": "id,topic,prompt,archived", "order": "created_at.asc"}
        if not include_archived:
            params["archived"] = "eq.false"
        resp = requests.get(
            f"{supabase_url}/rest/v1/{PROMPTS_TABLE}",
            params=params,
            headers=_headers(supabase_key),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def add_prompt(supabase_url, supabase_key, topic, prompt):
    if not configured(supabase_url, supabase_key):
        return False
    try:
        resp = requests.post(
            f"{supabase_url}/rest/v1/{PROMPTS_TABLE}",
            headers={**_headers(supabase_key), "Prefer": "return=minimal"},
            json={"topic": topic, "prompt": prompt, "archived": False},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def set_prompt_archived(supabase_url, supabase_key, prompt_id, archived):
    if not configured(supabase_url, supabase_key):
        return False
    try:
        resp = requests.patch(
            f"{supabase_url}/rest/v1/{PROMPTS_TABLE}",
            params={"id": f"eq.{prompt_id}"},
            headers={**_headers(supabase_key), "Prefer": "return=minimal"},
            json={"archived": archived},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def delete_prompt(supabase_url, supabase_key, prompt_id):
    if not configured(supabase_url, supabase_key):
        return False
    try:
        resp = requests.delete(
            f"{supabase_url}/rest/v1/{PROMPTS_TABLE}",
            params={"id": f"eq.{prompt_id}"},
            headers=_headers(supabase_key),
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False
