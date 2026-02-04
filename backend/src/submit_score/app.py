import json
import os
import time
import boto3
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")
SCORES_TABLE_NAME = os.environ["SCORES_TABLE_NAME"]
ANSWER_HISTORY_TABLE_NAME = os.environ["ANSWER_HISTORY_TABLE_NAME"]

scores_table = dynamodb.Table(SCORES_TABLE_NAME)
history_table = dynamodb.Table(ANSWER_HISTORY_TABLE_NAME)

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
        question_id = (body.get("questionId") or "").strip()
        delta = body.get("delta")
        
        # 採点結果の詳細情報（オプション）
        result = body.get("result")  # correct/close/incorrect
        score = body.get("score")  # 0.0-1.0
        feedback = body.get("feedback")

        if not team_id:
            return _resp(400, {"message": "teamId is required"})
        if not team_name:
            return _resp(400, {"message": "teamName is required"})
        if not question_id:
            return _resp(400, {"message": "questionId is required"})
        if delta is None:
            return _resp(400, {"message": "delta is required"})
        
        delta_num = Decimal(str(delta))
        now_ms = int(time.time() * 1000)

        # 回答履歴を確認
        history_key = {"teamId": team_id, "questionId": question_id}
        history_resp = history_table.get_item(Key=history_key)
        existing_history = history_resp.get("Item")

        is_first_answer = existing_history is None
        
        # 回答履歴を更新（初回・再評価共通）
        history_item = {
            "teamId": team_id,
            "questionId": question_id,
            "teamName": team_name,
            "latestDelta": delta_num,
            "latestResult": result,
            "latestScore": Decimal(str(score)) if score is not None else None,
            "latestFeedback": feedback,
            "updatedAt": now_ms,
            "attemptCount": (existing_history.get("attemptCount", 0) if existing_history else 0) + 1,
        }
        
        # 初回のみ初回スコアを記録
        if is_first_answer:
            history_item["firstDelta"] = delta_num
            history_item["firstResult"] = result
            history_item["firstScore"] = Decimal(str(score)) if score is not None else None
            history_item["firstAnsweredAt"] = now_ms
        else:
            # 既存の初回データを保持
            history_item["firstDelta"] = existing_history.get("firstDelta")
            history_item["firstResult"] = existing_history.get("firstResult")
            history_item["firstScore"] = existing_history.get("firstScore")
            history_item["firstAnsweredAt"] = existing_history.get("firstAnsweredAt")
        
        history_table.put_item(Item=history_item)

        # スコアテーブルの更新（初回のみ加点）
        if is_first_answer:
            # 初回回答：スコアを加算
            res = scores_table.update_item(
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
            total_score = int(item.get("score", 0))
            
            return _resp(200, {
                "ok": True,
                "teamId": team_id,
                "teamName": item.get("teamName"),
                "score": total_score,
                "updatedAt": now_ms,
                "isFirstAnswer": True,
                "message": "初回回答として加点しました"
            })
        else:
            # 再評価：スコアは変更せず、評価履歴のみ更新
            # 現在のスコアを取得
            score_resp = scores_table.get_item(Key={"teamId": team_id})
            score_item = score_resp.get("Item", {})
            total_score = int(score_item.get("score", 0))
            
            return _resp(200, {
                "ok": True,
                "teamId": team_id,
                "teamName": team_name,
                "score": total_score,
                "updatedAt": now_ms,
                "isFirstAnswer": False,
                "firstDelta": int(existing_history.get("firstDelta", 0)),
                "latestDelta": int(delta_num),
                "attemptCount": history_item["attemptCount"],
                "message": "再評価しました（スコアは初回のまま維持）"
            })

    except Exception as e:
        return _resp(500, {"message": "internal error", "detail": str(e)})
