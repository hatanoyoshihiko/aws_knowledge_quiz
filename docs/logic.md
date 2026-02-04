# 主なロジック

## rubric(採点基準)について

本アプリでは、AWS Bedrock に **rubric（採点基準）** を渡し、LLM によって回答内容を評価しています。  
rubric は「どの観点で、どこまで満たしていれば正解か」を定義するための構造です。

## rubric の基本構造

rubric は複数の **評価ポイント（Point）** から構成され、LLMに渡す採点基準定義となります。

```json
{
  "expectedAnswer": "期待される回答の例（240字以内）",
  "mustHavePoints": [
    {
      "id": "p1",
      "label": "必須要点のラベル（35字以内）",
      "keywords_any": ["キーワード1", "キーワード2"],
      "notes": "補足説明（70字以内）"
    }
  ],
  "niceToHavePoints": [
    {
      "id": "n1",
      "label": "加点要素のラベル",
      "keywords_any": ["キーワード"],
      "notes": "補足説明"
    }
  ],
  "commonWrongClaims": [
    {
      "id": "w1",
      "label": "よくある誤解",
      "keywords_any": ["誤ったキーワード"],
      "notes": "なぜ誤りか"
    }
  ],
  "scoringPolicy": {
    "correct_threshold": 1.0,
    "close_threshold": 0.8,
    "must_points_total": 4,
    "close_if_must_points_met_at_least": 3,
    "correct_if_must_points_met_at_least": 4
  }
}
```

### mustHavePoints（必須要点）
- 正解と判定されるために必須の要素
- 通常4個生成される
- 各要点にはid、label、keywords_any、notesが含まれる
- keywords_anyは各10字以内、labelは35字以内、notesは70字以内

### niceToHavePoints（加点要素）
- 満たしていると加点される要素（オプション）
- 0〜1個生成される
- 必須ではないが、より深い理解を示す内容

### commonWrongClaims（よくある誤解）
- 回答者が陥りやすい誤った理解（オプション）
- 0〜1個生成される
- これらが含まれていると減点される可能性がある

### scoringPolicy（採点ポリシー）
- **correct_threshold**: 1.0（100%充足で正解）
- **close_threshold**: 0.8（80%充足で惜しい）
- **must_points_total**: 必須要点の総数（通常4）
- **close_if_must_points_met_at_least**: 惜しいと判定される最小充足数（通常3）
- **correct_if_must_points_met_at_least**: 正解と判定される最小充足数（通常4）

## P（評価ポイント）について

- P1 / P2 / P3 / P4 ... はコード内で定義しているものではありません
- Bedrock が mustHavePoints 配列の順番から 自動採番 します
- P の最大数は mustHavePoints の要素数分で通常4個

## 採点結果の考え方

Bedrock は各 P について「満たしているか（met）」を判定し、全体のスコア（0.0〜1.0）を返します。

```json
{
  "result": "correct",
  "score": 1.0,
  "mustPointsMet": ["p1", "p2", "p3", "p4"],
  "missingMustPoints": [],
  "feedback": "素晴らしい回答です！...",
  "nextHint": "さらに深掘りするなら..."
}
```

アプリ側では、
- mustPointsMet の数
- scoringPolicy の閾値
- 返却された score

をもとに、最終的な正誤判定（correct/close/incorrect）を行います。

## クイズ生成フロー

### 1. MCP検索クエリの決定的生成
- カテゴリ（security、networking、storage、serverless、well-architected）ごとに定義された部品から組み合わせを生成
- 部品: services（サービス名）、topics（トピック）、angles（観点）
- カーソルベースでインデックスを管理し、同じクエリの繰り返しを回避
- クエリ空間サイズ = services数 × topics数 × angles数

### 2. AWS Knowledge MCP Serverからの情報取得
- 生成されたクエリでMCP検索を実行
- 最大3件のスニペットを取得（SOURCE_SNIPPETS_MAX: 3）
- 合計1500文字以内に制限（SOURCE_CONTEXT_MAX_CHARS: 1500）

### 3. Bedrock Prompt Managementでクイズ生成
- プロンプト変数:
  - category: カテゴリ
  - level: 難易度（100=基礎、200=設計、300=実装、400=専門家）
  - avoid_duplicate_hint: 最近15問のヒント（タイトル、タグ、論点）
  - question_style: 出題スタイル（使い分け、トレードオフ、誤り探し等）
  - style_guidance: スタイル別の出題方針
  - source_context: MCP検索結果
- MaxTokens: 2400、Temperature: 0.15
- JSON形式で出力（コードフェンス除去処理あり）

### 4. 重複チェックと保存
- タイトル、本文、必須要点からハッシュ値を生成
- DynamoDBに条件付き書き込み（重複時は失敗）
- 成功時はカーソルを進める（次回は異なるクエリを使用）

### 5. リトライとタイムアウト
- MAX_MCP_REFRESH: 2（異なるMCPクエリで最大2回再試行）
- 残り時間35秒未満で生成ループを停止
- 重複時は503エラーを返し、クライアント側で再試行を促す

## 回答判定フロー

### 1. 問題情報の取得
- questionIdでDynamoDBから問題とrubricを取得

### 2. Bedrockでの採点
- SYSTEM_PROMPT: 採点者の役割とルールを定義
- USER_PROMPT: 問題本文、rubric、ユーザー回答を含む
- 出力: result、score、mustPointsMet、missingMustPoints、feedback、nextHint

### 3. 採点結果の検証
- mustPointsMetとmissingMustPointsの整合性チェック
- スコアの範囲チェック（0.0〜1.0）
- scoringPolicyとの整合性確認

### 4. スコア反映
- 初回回答: ScoresTableにスコアを加算
- 再評価: AnswerHistoryTableのみ更新（スコアは変更しない）
- 回答履歴に詳細情報を記録（result、score、feedback、試行回数）

## 出題されたクイズがチーム画面で共通に出力される仕組み

- host側で生成したクイズをDynamoDBに保存し、今の出題(current)として参照できる状態にしています
- team UIはGET /quiz/currentでDynamoDBのGSI_Recentから最新の1件を取得し、これをcurrentのクイズとして扱うだけです
  - `get_current_quiz/app.py` の `resp = repo.table.query` が具体的な判定処理です
- 15秒ごとにポーリングして最新状態を取得

## 最適化の詳細

### タイムアウト対策
- Lambda実行時間: 60秒
- Lambdaメモリ: 2048MB（GetNextQuizFunction）
- Bedrock接続タイムアウト: 5秒
- Bedrock読み取りタイムアウト: 25秒
- 残り時間チェック: 35秒未満で生成ループを停止

### クイズ生成の多様性
- MAX_MCP_REFRESH: 2（異なるMCPクエリで再試行）
- Temperature: 0.15（多様性と品質のバランス）
- カーソルベースのクエリローテーション
- 重複回避ヒント: 最近15問（DUPLICATE_HINT_WINDOW: 15）

### JSON出力の安定化
- MaxTokens: 2400（完全なJSON出力を保証）
- 文字数制約:
  - title: ≤55字
  - body: ≤240字
  - expectedAnswer: ≤240字
  - sourceSummary: ≤160字
  - mustHavePoints.label: ≤35字
  - mustHavePoints.notes: ≤70字
  - keywords_any: ≤10字
- JSON救済処理: コードフェンス除去、外側オブジェクト抽出

### フィードバック
- 最大長: 400字
- トンチを利かせたユーモアに富んだ文章
- 必須要点の充足状況を明確に伝える

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
