from __future__ import annotations
import json
import boto3

class BedrockClient:
    def __init__(self):
        self._client = boto3.client("bedrock-runtime")

    def converse_json(self, *, model_id: str, system: str, user: str) -> str:
        # Converse API（文字列JSONを返すようプロンプトで強制）
        resp = self._client.converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"temperature": 0.2, "maxTokens": 2200},
        )
        # 応答テキストを連結
        parts = []
        for c in resp["output"]["message"]["content"]:
            if "text" in c:
                parts.append(c["text"])
        return "".join(parts).strip()
