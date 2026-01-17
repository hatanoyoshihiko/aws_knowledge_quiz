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

MAX_ATTEMPTS = int(env("MAX_ATTEMPTS", "3"))
MAX_MCP_REFRESH = int(env("MAX_MCP_REFRESH", "2"))
DUPLICATE_HINT_WINDOW = int(env("DUPLICATE_HINT_WINDOW", "20"))

SOURCE_CONTEXT_MAX_CHARS = int(env("SOURCE_CONTEXT_MAX_CHARS", "3000"))
SOURCE_SNIPPETS_MAX = int(env("SOURCE_SNIPPETS_MAX", "6"))

MCP_ENDPOINT = env("MCP_ENDPOINT", "CHANGE_ME")
MCP_API_KEY = env("MCP_API_KEY", "")

# Host-only operations (e.g., /quiz/next) can require this shared secret.
# Keep it out of frontend assets. Set via SAM parameter HostKey.
HOST_KEY = env("HOST_KEY", "")
LOG_LEVEL = env("LOG_LEVEL", "INFO")
