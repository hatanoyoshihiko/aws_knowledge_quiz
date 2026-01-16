import json
import os
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
            "Access-Control-Allow-Methods": "OPTIONS,GET",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }

def _to_int(x):
    if isinstance(x, Decimal):
        return int(x)
    return int(x or 0)

def handler(event, context):
    # CORS preflight
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return _resp(200, {"ok": True})

    try:
        # 小規模なら Scan + ソートで十分
        # チーム数が増えるなら GSI( score ) を切って Query にする
        res = table.scan(ProjectionExpression="teamId, teamName, score, updatedAt")
        items = res.get("Items", [])

        normalized = []
        for it in items:
            normalized.append({
                "teamId": it.get("teamId"),
                "teamName": it.get("teamName", ""),
                "score": _to_int(it.get("score", 0)),
                "updatedAt": _to_int(it.get("updatedAt", 0)),
            })

        normalized.sort(key=lambda x: (-x["score"], -x["updatedAt"], x["teamName"]))

        return _resp(200, {"ok": True, "items": normalized})

    except Exception as e:
        return _resp(500, {"message": "internal error", "detail": str(e)})
