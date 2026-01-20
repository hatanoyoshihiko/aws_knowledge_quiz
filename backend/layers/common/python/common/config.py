from __future__ import annotations
import os

def env(name: str, default: str | None = None) -> str:
    v = os.getenv(name, default)
    if v is None:
        raise RuntimeError(f"Missing env var: {name}")
    return v

STAGE_NAME = env("STAGE_NAME", "dev")
BEDROCK_MODEL_ID = env("BEDROCK_MODEL_ID")
QUIZ_TABLE_NAME = env("QUIZ_TABLE_NAME")

MAX_ATTEMPTS = int(env("MAX_ATTEMPTS", "1")) #同一の MCP 検索結果（SOURCE_CONTEXT）に対して、Bedrock によるクイズ生成を何回まで再試行するか、低いと生成時間が短くなります
MAX_MCP_REFRESH = int(env("MAX_MCP_REFRESH", "0")) #クイズ生成に失敗した場合に、別の MCP 検索クエリで再検索する最大回数
DUPLICATE_HINT_WINDOW = int(env("DUPLICATE_HINT_WINDOW", "20"))

SOURCE_CONTEXT_MAX_CHARS = int(env("SOURCE_CONTEXT_MAX_CHARS", "2200"))
SOURCE_SNIPPETS_MAX = int(env("SOURCE_SNIPPETS_MAX", "3"))

MCP_ENDPOINT = env("MCP_ENDPOINT", "CHANGE_ME")
MCP_API_KEY = env("MCP_API_KEY", "")

# Host-only operations (e.g., /quiz/next) can require this shared secret.
# Keep it out of frontend assets. Set via SAM parameter HostKey.
HOST_KEY = env("HOST_KEY", "")
LOG_LEVEL = env("LOG_LEVEL", "INFO")
