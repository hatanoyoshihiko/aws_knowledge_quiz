from __future__ import annotations
import time
import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

class QuizRepo:
    def __init__(self, table_name: str):
        self.ddb = boto3.resource("dynamodb")
        self.table = self.ddb.Table(table_name)

    def put_unique(self, item: dict) -> bool:
        try:
            self.table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(QuestionHash)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def get_by_hash(self, question_hash: str) -> dict | None:
        resp = self.table.get_item(Key={"QuestionHash": question_hash})
        return resp.get("Item")

    def get_recent_hints(self, limit: int) -> dict:
        """
        直近N件から重複回避ヒントを組み立てる。
        - recentTitles: 直近のタイトル
        - recentTags: 直近のタグ（重複除去）
        - recentMustLabels: 直近のmustポイントlabel（重複除去）
        """
        try:
            resp = self.table.query(
                IndexName="GSI_Recent",
                KeyConditionExpression=Key("GSI1PK").eq("RECENT"),
                ScanIndexForward=False,  # newest first
                Limit=max(1, limit),
            )
        except ClientError as e:
            # GSI未反映などで失敗してもアプリは動かす（ヒントなしで継続）
            return {"recentTitles": [], "recentTags": [], "recentMustLabels": []}

        items = resp.get("Items", [])
        recent_titles: list[str] = []
        tags_set: set[str] = set()
        must_labels_set: set[str] = set()

        for it in items:
            # title
            q = it.get("Question") or {}
            title = q.get("Title")
            if isinstance(title, str) and title.strip():
                recent_titles.append(title.strip())

            # tags
            tags = it.get("Tags")
            if isinstance(tags, list):
                for t in tags:
                    if isinstance(t, str) and t.strip():
                        tags_set.add(t.strip())

            # must labels
            rubric = it.get("Rubric") or {}
            must_points = rubric.get("mustHavePoints") or rubric.get("MustHavePoints")
            # (保存形式が揺れない前提なら片方だけでOK。安全に両方見ます)
            if isinstance(must_points, list):
                for mp in must_points:
                    if isinstance(mp, dict):
                        lab = mp.get("label") or mp.get("Label")
                        if isinstance(lab, str) and lab.strip():
                            must_labels_set.add(lab.strip())

        return {
            "recentTitles": recent_titles[:limit],
            "recentTags": sorted(tags_set),
            "recentMustLabels": sorted(must_labels_set),
        }

def now_iso_jst() -> str:
    # 実態はUTCのISO(Z)なので、後で now_iso_utc などにリネーム推奨
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
