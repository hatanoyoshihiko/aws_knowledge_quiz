from __future__ import annotations
import boto3

class BedrockClient:
    def __init__(self):
        self._rt = boto3.client("bedrock-runtime")
        self._agent = boto3.client("bedrock-agent")
        self._cached_prompt_version_arn: str | None = None

    def resolve_latest_prompt_version_arn(self, *, prompt_name: str) -> str:
        """
        Resolve latest PromptVersion ARN by prompt name.
        Cached per execution environment.
        """
        if self._cached_prompt_version_arn:
            return self._cached_prompt_version_arn

        if not prompt_name.strip():
            raise ValueError("prompt_name is empty")

        # 1) find prompt by name
        next_token = None
        prompt_arn = None
        while True:
            kwargs = {"maxResults": 50}
            if next_token:
                kwargs["nextToken"] = next_token
            resp = self._agent.list_prompts(**kwargs)

            for p in resp.get("promptSummaries", []):
                if (p.get("name") or "").strip() == prompt_name.strip():
                    prompt_arn = p.get("arn")
                    break
            if prompt_arn:
                break

            next_token = resp.get("nextToken")
            if not next_token:
                break

        if not prompt_arn:
            raise ValueError(f"Prompt not found: name={prompt_name}")

        # 2) list versions and pick max version
        next_token = None
        latest = None  # dict with arn, version
        while True:
            kwargs = {"promptArn": prompt_arn, "maxResults": 50}
            if next_token:
                kwargs["nextToken"] = next_token
            resp = self._agent.list_prompt_versions(**kwargs)

            for v in resp.get("promptVersionSummaries", []):
                ver = v.get("version")
                arn = v.get("arn")
                if ver is None or arn is None:
                    continue
                if (latest is None) or (int(ver) > int(latest["version"])):
                    latest = {"version": int(ver), "arn": arn}

            next_token = resp.get("nextToken")
            if not next_token:
                break

        if not latest:
            raise ValueError(f"No PromptVersion exists for prompt: {prompt_arn}")

        self._cached_prompt_version_arn = latest["arn"]
        return latest["arn"]

    def converse_prompt_json(
        self,
        *,
        prompt_arn: str,
        prompt_variables: dict[str, str],
        messages: list[dict] | None = None,
    ) -> str:
        pv = {k: {"text": v} for k, v in (prompt_variables or {}).items()}

        req: dict = {"modelId": prompt_arn, "promptVariables": pv}
        if messages:
            req["messages"] = messages

        resp = self._rt.converse(**req)

        parts: list[str] = []
        for c in resp["output"]["message"]["content"]:
            if "text" in c:
                parts.append(c["text"])
        return "".join(parts).strip()
