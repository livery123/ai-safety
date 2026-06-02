#!/usr/bin/env python3
"""
连通性自检：Chat + Embedding（读 .env 中 LLM_BASE_URL / DASHSCOPE_API_KEY / 模型名）。

用法:
  python scripts/test_llm_api.py
  python scripts/test_llm_api.py --embedding-model text-embedding-v4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import (  # noqa: E402
    AIHUBMIX_APP_CODE,
    API_KEY,
    BASE_URL,
    EMBEDDING_MODEL,
    LLM_MODEL,
)
from core.llm_client import OpenAICompatibleBackend  # noqa: E402


def _mask(s: str) -> str:
    s = (s or "").strip()
    if len(s) <= 8:
        return "***"
    return f"{s[:4]}...{s[-4:]}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Test LLM Chat + Embedding connectivity")
    parser.add_argument("--embedding-model", default="", help="Override EMBEDDING_MODEL for this run")
    args = parser.parse_args()

    embed_model = (args.embedding_model or EMBEDDING_MODEL or "").strip()
    print("=== LLM API 连通性测试 ===")
    print(f"BASE_URL={BASE_URL}")
    print(f"LLM_MODEL={LLM_MODEL}")
    print(f"EMBEDDING_MODEL={embed_model}")
    print(f"API_KEY={_mask(API_KEY)}")
    if AIHUBMIX_APP_CODE:
        print(f"AIHUBMIX_APP_CODE={_mask(AIHUBMIX_APP_CODE)}")

    if not API_KEY:
        print("ERROR: DASHSCOPE_API_KEY 未配置", file=sys.stderr)
        sys.exit(1)

    be = OpenAICompatibleBackend()
    failed = False

    try:
        reply = be.chat_completion(
            [{"role": "user", "content": "只回复一个字：好"}],
            temperature=0,
            timeout=120,
        )
        print(f"OK Chat: {reply.strip()[:120]!r}")
    except Exception as e:
        failed = True
        print(f"FAIL Chat: {type(e).__name__}: {e}", file=sys.stderr)

    try:
        vecs = be.embed_texts(["AI安全向量测试"], model=embed_model or None, timeout=120)
        dim = len(vecs[0]) if vecs else 0
        print(f"OK Embedding: dim={dim}")
    except Exception as e:
        failed = True
        print(f"FAIL Embedding: {type(e).__name__}: {e}", file=sys.stderr)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
