import json
import os
import time
import boto3
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ["SCORES_TABLE_NAME"]
table = dynamodb.Table(TABLE_NAME)

def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "OPTIONS,POST",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }

def handler(event, context):
    # CORS preflight
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return _resp(200, {"ok": True})

    try:
        body = json.loads(event.get("body") or "{}")
        team_id = (body.get("teamId") or "").strip()
        team_name = (body.get("teamName") or "").strip()
        delta = body.get("delta")

        if not team_id:
            return _resp(400, {"message": "teamId is required"})
        if not team_name:
            return _resp(400, {"message": "teamName is required"})
        if delta is None:
            return _resp(400, {"message": "delta is required"})
        # delta は 0〜100 くらいを想定（必要なら制限）
        delta_num = Decimal(str(delta))

        now_ms = int(time.time() * 1000)

        # score を原子的に加算（同時更新でも破綻しにくい）
        # teamName は常に最新で上書き（入力揺れが嫌なら最初だけ固定でもOK）
        res = table.update_item(
            Key={"teamId": team_id},
            UpdateExpression="SET teamName=:n, updatedAt=:t ADD score :d",
            ExpressionAttributeValues={
                ":n": team_name,
                ":t": now_ms,
                ":d": delta_num,
            },
            ReturnValues="ALL_NEW",
        )

        item = res["Attributes"]
        # Decimal -> float/int へ
        score = int(item.get("score", 0))
        return _resp(200, {"ok": True, "teamId": team_id, "teamName": item.get("teamName"), "score": score, "updatedAt": now_ms})

    except Exception as e:
        return _resp(500, {"message": "internal error", "detail": str(e)})
