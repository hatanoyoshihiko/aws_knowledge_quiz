# 主なロジック

## rubric(採点基準)について

本アプリでは、AWS Bedrock に **rubric（採点基準）** を渡し、LLM によって回答内容を評価しています。  
rubric は「どの観点で、どこまで満たしていれば正解か」を定義するための構造です。

## rubric の基本構造

- rubric は複数の **評価ポイント（Point）** から構成され、LLMに渡す採点基準定義となります
- points 配列の 各要素が1つの評価観点
- 配列の順番に応じて、Bedrock 側で自動的にP1, P2, P3 ... が割り当てられます

```json
"rubric": {
  "points": [
    {
      "description": "〇〇について正しく説明している",
      "must": true
    },
    {
      "description": "△△の理由に触れている",
      "must": false
    }
  ]
}
```

## P（評価ポイント）について

- P1 / P2 / P3 ... はコード内で定義しているものではありません
- Bedrock が points 配列の順番から 自動採番 します
- P の最大数は points の要素数分で3～6個程度で生成します

## must フラグの意味

- 各評価ポイントには must フラグを設定できます。
- 設定値 意味
  - must: true 必須条件（満たしていないと正解にならない）
  - must: false 任意条件（加点要素）
  - must: true のポイントは 最低限満たすべき条件

必須ポイントを満たしていない場合、スコアが一定以上でも不正解になることがあります

## 採点結果の考え方

- Bedrock は各 P について「満たしているか（met）」を判定し、
- 全体のスコア（0.0〜1.0）を返します。

```json
{
  "points": {
    "P1": { "met": true },
    "P2": { "met": false }
  },
  "score": 0.5
}
```

アプリ側では、

- met = true の数
- must: true の達成率
- 返却された scoreをもとに、最終的な正誤判定を行います。

## 出題されたクイズがチーム画面で共通に出力される仕組

- host側で生成したクイズをDynamoDBに保存し、今の出題(current)として参照できる状態にしています
- team UIはGet /quiz/currentでDynamoDBのGSIから最新の1件を取得し、これをcurrentのクイズとして扱うだけです
  - `get_cyrrent_quiz/app.py` の  `resp = repo.table.query` が具体的な判定処理です

## セキュリティ

| 対象 | 対策 |
| --- | --- |
| CloudFront | 特になし。任意でWAFをアタッチして下さい |
| API Gateway | CloudFrontのカスタムヘッダーの値をLambda Authorizerで判定し、このヘッダーと値が一致しないリクエストからのアクセスのみ許可します |

## ファイル別 役割一覧（処理順）

| ファイル名                                      | 処理順 | 役割 / 制御内容（概要）                                   | 主に関わるフロー         |
| ------------------------------------------ | --: | ----------------------------------------------- | ---------------- |
| `frontend/public/host.html`                |   0 | **出題者（Host）用UI**。次のクイズ生成、現在クイズ・スコアの表示制御         | NextQuiz / Sync  |
| `frontend/public/team.html`                |   1 | **回答者（Team）用UI**。回答入力、採点結果表示、状態同期               | Judge / Sync     |
| `frontend/public/config.json`              |   2 | フロントエンド設定（API Endpoint、Stage、HostKey 等）         | 全体               |
| `frontend/template.yaml`                   |   3 | フロントエンド（S3 + CloudFront）の IaC 定義                | 全体               |
| `backend/template.yaml`                    |   4 | API Gateway、Lambda、DynamoDB、権限、環境変数の IaC 定義     | 全体               |
| `backend/requirements.txt`                 |   5 | Lambda 実行環境の Python 依存関係定義                      | 全体               |
| `backend/src/get_next_quiz/app.py`         |   6 | **出題API本体**。MCP検索 → Bedrock生成 → クイズ状態保存 → レスポンス | NextQuiz         |
| `backend/src/judge_answer/app.py`          |   7 | **採点API本体**。入力正規化 → MCP検索 → Bedrock判定 → 検証 → 保存 | Judge            |
| `backend/src/get_current_quiz/app.py`      |   8 | **現在出題中クイズ取得API**。Host/Team 両方から参照される           | Sync             |
| `backend/src/get_scores/app.py`            |   9 | **スコア一覧取得API**。全チームのスコアを集計・返却                   | Sync             |
| `backend/src/common/config.py`             |  10 | 環境変数・定数定義（テーブル名、モデルID、閾値など）                     | 全体               |
| `backend/src/common/schema.py`             |  11 | 入出力スキーマ定義（quiz / answer / result / rubric 等）    | NextQuiz / Judge |
| `backend/src/common/normalize.py`          |  12 | ユーザー入力の正規化（空白、改行、表記揺れ補正）                        | Judge            |
| `backend/src/common/validate.py`           |  13 | 採点結果の整合性検証（スコア範囲、mustPointsMet 比率など）            | Judge            |
| `backend/src/common/ddb.py`                |  14 | DynamoDB アクセス層（クイズ状態・履歴・スコアの CRUD）              | 全体               |
| `backend/src/common/bedrock.py`            |  15 | Bedrock 実行ラッパー（プロンプト構築、推論、レスポンス整形）              | NextQuiz / Judge |
| `backend/src/common/mcp.py`                |  16 | AWS Knowledge MCP Server 呼び出し補助（検索・取得処理）        | NextQuiz / Judge |
| `backend/src/common/errors.py`             |  17 | API 用例外・エラー整形（HTTP ステータス・メッセージ）                 | 全体               |
| `backend/src/common/__init__.py`           |  18 | common パッケージ初期化                                 | 全体               |
| `backend/src/get_next_quiz/__init__.py`    |  19 | get_next_quiz パッケージ初期化                          | NextQuiz         |
| `backend/src/judge_answer/__init__.py`     |  20 | judge_answer パッケージ初期化                           | Judge            |
| `backend/src/get_current_quiz/__init__.py` |  21 | get_current_quiz パッケージ初期化                       | Sync             |
| `backend/src/get_scores/__init__.py`       |  22 | get_scores パッケージ初期化                             | Sync             |
