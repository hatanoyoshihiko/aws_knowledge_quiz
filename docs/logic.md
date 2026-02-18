# 主なロジック

## Lambda関数の構成

### クイズ生成関連

| 関数名 | タイムアウト | メモリ | 役割 | エンドポイント |
| --- | --- | --- | --- | --- |
| StartQuizGenerationFunction | 15秒 | 256MB | 非同期クイズ生成開始 | GET /quiz/generate |
| GetNextQuizFunction | 60秒 | 512MB | クイズ生成実行 | GET /quiz/next |

### クイズ取得関連

| 関数名 | タイムアウト | メモリ | 役割 | エンドポイント |
| --- | --- | --- | --- | --- |
| GetCurrentQuizFunction | 60秒 | 512MB | 現在出題中クイズ取得 | GET /quiz/current |
| GetQuizByIdFunction | 10秒 | 512MB | 過去問取得 | GET /quiz/question |

### 採点・スコア関連

| 関数名 | タイムアウト | メモリ | 役割 | エンドポイント |
| --- | --- | --- | --- | --- |
| JudgeAnswerFunction | 60秒 | 512MB | 回答採点 | POST /quiz/answer |
| GenerateExampleAnswerFunction | 60秒 | 512MB | 回答例生成 | GET /quiz/example-answer |
| SubmitScoreFunction | 60秒 | 512MB | スコア送信 | POST /scores/submit |
| GetScoresFunction | 60秒 | 512MB | スコアボード取得 | GET /scores |

### Lambda Layer

| Layer名 | 内容 | 使用関数 |
| --- | --- | --- |
| CommonLayer | 共通モジュール（config、ddb、bedrock、mcp、errors、schema、normalize、validate） | 全Lambda関数 |

### 環境変数（Globals）

| 変数名 | 説明 | デフォルト値 |
| --- | --- | --- |
| STAGE_NAME | ステージ名 | dev |
| BEDROCK_MODEL_ID | Bedrockモデル | jp.anthropic.claude-sonnet-4-5-20250929-v1:0 |
| BEDROCK_GUARDRAIL_IDENTIFIER | Guardrail ID | （空） |
| BEDROCK_GUARDRAIL_VERSION | Guardrail Version | DRAFT |
| QUIZ_TABLE_NAME | クイズテーブル名 | （自動生成） |
| MAX_ATTEMPTS | 最大試行回数 | 1 |
| MAX_MCP_REFRESH | MCP再試行回数 | 0 |
| DUPLICATE_HINT_WINDOW | 重複回避ウィンドウ | 20 |
| SOURCE_CONTEXT_MAX_CHARS | MCP最大文字数 | 2200 |
| SOURCE_SNIPPETS_MAX | MCP最大スニペット数 | 3 |
| MCP_ENDPOINT | MCP Server URL | https://knowledge-mcp.global.api.aws |
| HOST_KEY | Host認証キー | （デプロイ時指定） |
| LOG_LEVEL | ログレベル | INFO |

### 関数固有の環境変数

| 関数名 | 変数名 | 説明 |
| --- | --- | --- |
| StartQuizGenerationFunction | GET_NEXT_QUIZ_FUNCTION_NAME | GetNextQuizFunctionの関数名 |
| GetNextQuizFunction | BEDROCK_PROMPT_ARN | Bedrock Prompt Management ARN |
| SubmitScoreFunction | SCORES_TABLE_NAME | スコアテーブル名 |
| SubmitScoreFunction | ANSWER_HISTORY_TABLE_NAME | 回答履歴テーブル名 |
| GetScoresFunction | SCORES_TABLE_NAME | スコアテーブル名 |

## rubric(採点基準)について

本アプリでは、AWS Bedrock に **rubric（採点基準）** を渡し、LLM で回答内容を評価。  
rubric は「どの観点で、どこまで満たしていれば正解か」を定義する構造。

## rubric の基本構造

rubric は複数の **評価ポイント（Point）** から構成され、LLMに渡す採点基準定義となる。

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
- 通常4個生成
- 各要点にはid、label、keywords_any、notesが含まれる
- keywords_anyは各10字以内、labelは35字以内、notesは70字以内

### niceToHavePoints（加点要素）
- 満たしていると加点される要素（オプション）
- 0〜1個生成
- 必須ではないが、より深い理解を示す内容

### commonWrongClaims（よくある誤解）
- 回答者が陥りやすい誤った理解（オプション）
- 0〜1個生成
- これらが含まれていると減点される可能性

### scoringPolicy（採点ポリシー）
- **correct_threshold**: 1.0（100%充足で正解）
- **close_threshold**: 0.8（80%充足で惜しい）
- **must_points_total**: 必須要点の総数（通常4）
- **close_if_must_points_met_at_least**: 惜しいと判定される最小充足数（通常3）
- **correct_if_must_points_met_at_least**: 正解と判定される最小充足数（通常4）

## P（評価ポイント）について

- P1 / P2 / P3 / P4 ... はコード内で定義していない
- Bedrock が mustHavePoints 配列の順番から 自動採番
- P の最大数は mustHavePoints の要素数分で通常4個

## 採点結果の考え方

Bedrock は各 P について「満たしているか（met）」を判定し、全体のスコア（0.0〜1.0）を返す。

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

アプリ側では、mustPointsMet の数、scoringPolicy の閾値、返却された score をもとに、最終的な正誤判定（correct/close/incorrect）を行う。

## クイズ生成フロー

クイズ生成は非同期で実行され、以下の2つのLambda関数で処理。

### StartQuizGenerationFunction（非同期開始）

**役割**: クイズ生成を非同期で開始し、即座にレスポンスを返却

**処理**:
1. Host Key認証
2. パラメータ解析（category、level）
3. GetNextQuizFunctionを非同期呼び出し
4. 202 Acceptedを即座に返却

**利点**: UIのブロッキングを回避、タイムアウトリスクの軽減

### GetNextQuizFunction（実際の生成処理）

**役割**: バックグラウンドでクイズを生成

**処理**:
1. カーソル取得（カテゴリ×レベル）
2. MCP検索クエリの生成
3. AWS Knowledge MCP Serverから情報取得
4. 最近20問のヒント取得
5. Bedrock Prompt Managementでクイズ生成
6. ハッシュ値による重複チェック
7. DynamoDBに保存
8. カーソル更新

詳細は [クイズ生成の仕組み](./quiz.md) を参照。

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

## 回答例生成フロー

### 1. 問題情報の取得
- questionIdでDynamoDBから問題とrubric（採点基準）を取得

### 2. Bedrockで回答例生成
- SYSTEM_PROMPT: 模範回答生成者の役割を定義
- USER_PROMPT: 問題本文、rubric（必須要点、加点要素）を含む
- 生成条件:
  - 必須要点（mustHavePoints）をすべて含む
  - 加点要素（niceToHavePoints）も考慮
  - よくある誤解（commonWrongClaims）を避ける
  - 200〜300字程度の簡潔な回答
  - 100点満点を獲得できる内容

### 3. 回答例の返却
- 生成された回答例をJSON形式で返却: `{"exampleAnswer": "..."}`
- Team UIで「回答例を生成」ボタン押下後に表示

### 4. パフォーマンス設定
- Lambda実行時間: 60秒、メモリ: 512MB
- Bedrock接続タイムアウト: 5秒、読み取りタイムアウト: 25秒
- MaxTokens: 800（200〜300字の回答生成に十分）
- Temperature: 0.3（適度な多様性と品質のバランス）

### 5. 使用シーン
- 採点後に「回答例を生成」ボタンが表示
- 学習目的で100点満点の回答を確認
- 自分の回答と比較して改善点を見つける
- 復習モードで過去問の模範回答を確認

## 出題されたクイズがチーム画面で共通に出力される仕組み

### 概要

Host側で生成したクイズをDynamoDBに保存し、Team UIが定期的にポーリングして最新のクイズを取得。

### DynamoDB設計（GSI_Recent）

**テーブル構造**:
```
QuizHistoryTable
├── Primary Key: QuestionHash (String)
└── GSI_Recent (Global Secondary Index)
    ├── Partition Key: GSI1PK = "RECENT" (固定値)
    └── Sort Key: GSI1SK = CreatedAt (ISO 8601形式)
```

**クイズ保存時の属性**:
```python
item = {
    "QuestionHash": "quiz:abc123...",  # Primary Key（ハッシュ値）
    "GSI1PK": "RECENT",                # GSI Partition Key（全クイズ共通）
    "GSI1SK": "2026-02-06T10:30:45+09:00",  # GSI Sort Key（作成日時）
    "Version": 1,
    "Category": "security",
    "Level": 200,
    "Question": {
        "Title": "IAMポリシーの評価順序",
        "Body": "..."
    },
    "Rubric": {...},
    "CreatedAt": "2026-02-06T10:30:45+09:00",
    "Tags": ["iam", "policy"]
}
```

**GSI_Recentの利点**:
- **O(1)で最新クイズを取得**: Scanではなく効率的なQuery
- **時系列順**: CreatedAtでソートされているため、最新1件を即座に取得
- **スケーラブル**: クイズ数が増えてもパフォーマンス低下なし

### バックエンド実装（GetCurrentQuizFunction）

**処理フロー**:
```python
# get_current_quiz/app.py
resp = repo.table.query(
    IndexName="GSI_Recent",
    KeyConditionExpression=Key("GSI1PK").eq("RECENT"),  # 全クイズを対象
    ScanIndexForward=False,  # CreatedAt（GSI1SK）の降順でソート
    Limit=1,                 # 先頭1件（最新）のみ取得
)

items = resp.get("Items") or []
if not items:
    return {"status": "empty", "message": "まだクイズが出題されていません"}

item = items[0]  # 最新のクイズ
return {
    "question": {
        "questionId": item["QuestionHash"],
        "title": item["Question"]["Title"],
        "body": item["Question"]["Body"],
        "category": item["Category"],
        "level": item["Level"],
        "createdAt": item["CreatedAt"]
    }
}
```

**ScanIndexForwardの役割**:
- DynamoDBのGSIは、Sort Key（GSI1SK = CreatedAt）で自動的にソートされる
- デフォルトは昇順（`ScanIndexForward=True`）→ 古いクイズが先頭
- `ScanIndexForward=False`で降順に → 新しいクイズが先頭
- `Limit=1`で先頭1件を取得するため、降順にしないと古いクイズを取得してしまう

**具体例**:
```
DynamoDB内のデータ（GSI1SK = CreatedAtでソート済み）:
- 2026-02-06T10:00:00+09:00  ← 古いクイズ
- 2026-02-06T10:15:00+09:00
- 2026-02-06T10:30:00+09:00  ← 最新のクイズ

ScanIndexForward=True（昇順）:
  → 10:00:00のクイズが返る ❌

ScanIndexForward=False（降順）:
  → 10:30:00のクイズが返る ✅
```

**レスポンス例**:
```json
{
  "question": {
    "questionId": "quiz:abc123...",
    "title": "IAMポリシーの評価順序",
    "body": "以下のシナリオで...",
    "category": "security",
    "level": 200,
    "createdAt": "2026-02-06T10:30:45+09:00"
  }
}
```

### フロントエンド実装（ポーリング）

**Host UI / Team UI共通**:
```javascript
// mainTeam.js / mainHost.js
const POLL_MS = 15000; // 15秒

async function _poll() {
    const fn = _getCurrentQuizFn();
    if (!fn) return;
    
    // 復習モード中はポーリングしない
    if ((state.quizMode || "live") === "review") return;
    
    try {
        await fn({silent: true}); // 静かに同期
    } catch (_) {
        // ポーリングでは失敗しても無視
    }
}

// 初回同期 + 定期ポーリング開始
if (currentFn) {
    await currentFn({silent: true, force: true});
    setInterval(_poll, POLL_MS);
}
```

**重複チェック（新しいクイズの検出）**:
```javascript
// quizApi.host.js - クイズ生成後のポーリング
const previousQuizId = state.questionId || null;

for (let i = 0; i < maxAttempts; i++) {
    await new Promise(resolve => setTimeout(resolve, 5000));
    
    const currentData = await fetchCurrentQuiz();
    
    if (currentData?.question) {
        const q = currentData.question;
        // 新しいクイズが生成されたかチェック（IDが変わっている）
        if (q.questionId && q.questionId !== previousQuizId) {
            console.log(`[quiz] new quiz detected: ${q.questionId}`);
            setQuestionView(q);
            toast("クイズを生成しました");
            return q;
        }
    }
}
```

### 同期フロー全体

```
1. Host UI: 「次のクイズ」ボタン押下
   ↓
2. POST /quiz/generate → StartQuizGenerationFunction
   ↓ 即座に202返却
3. Host UI: ポーリング開始（5秒ごと、最大12回）
   ↓
4. バックグラウンド: GetNextQuizFunction実行
   - MCP検索（3〜5秒）
   - Bedrock生成（15〜25秒）
   - DynamoDB保存（GSI1PK="RECENT", GSI1SK=現在時刻）
   ↓
5. Host UI: GET /quiz/current でポーリング
   - questionIdが変わっていれば新しいクイズを表示
   ↓
6. Team UI: 15秒ごとのポーリングで自動同期
   - GET /quiz/current → GSI_Recentから最新1件取得
   - questionIdが変わっていれば画面更新
```

### タイミング図

```
時刻    Host UI              Backend                Team UI
0秒     [次のクイズ]押下
        ↓
0.5秒   ← 202 Accepted
        ポーリング開始
        ↓                    クイズ生成開始
5秒     GET /quiz/current →  (まだ古いクイズ)
        ↓
10秒    GET /quiz/current →  (まだ古いクイズ)
        ↓
15秒    GET /quiz/current →  (まだ古いクイズ)    GET /quiz/current
        ↓                                        ↓
20秒    GET /quiz/current →  (まだ古いクイズ)    (古いクイズ)
        ↓
25秒    GET /quiz/current →  (まだ古いクイズ)
        ↓
30秒    GET /quiz/current →  [新しいクイズ保存]
        ↓                    ↓
        ← 新しいクイズ        GSI1PK="RECENT"
        画面更新完了          GSI1SK=30秒時点
                             ↓
45秒                                            GET /quiz/current
                                                ↓
                                                ← 新しいクイズ
                                                画面更新完了
```

### ポーリング方式の利点

1. **シンプル**: WebSocketやServer-Sent Eventsが不要
2. **スケーラブル**: クライアント数が増えてもバックエンドの負荷は一定
3. **リアルタイム性**: 最大15秒の遅延で全クライアントが同期
4. **復習モード対応**: ポーリングを停止して過去問を表示可能
5. **エラー耐性**: 一時的な通信エラーでも次のポーリングで回復

### パフォーマンス最適化

- **DynamoDB**: GSI_Recentで効率的なクエリ（Scanではない）
- **Lambda**: GetCurrentQuizFunctionは軽量（10秒タイムアウト、512MB）
- **フロントエンド**: silent: trueでエラートーストを抑制
- **復習モード**: ポーリングを停止して不要なAPI呼び出しを削減

## 最適化の詳細

### タイムアウト対策
- Lambda実行時間: 60秒
- Lambdaメモリ: 2048MB（GetNextQuizFunction）
- Bedrock接続タイムアウト: 5秒
- Bedrock読み取りタイムアウト: 25秒
- 残り時間チェック: 35秒未満で生成ループを停止

### クイズ生成の多様性
- MAX_MCP_REFRESH: 0（デフォルト、環境変数で変更可能）
- Temperature: 0.15（多様性と品質のバランス）
- カーソルベースのクエリローテーション
- 重複回避ヒント: 最近20問（DUPLICATE_HINT_WINDOW: 20）

### JSON出力の安定化
- MaxTokens: 3500（完全なJSON出力を保証）
- 文字数制約:
  - title: ≤80字
  - body: ≤300字
  - expectedAnswer: ≤400字
  - sourceSummary: ≤250字
  - mustHavePoints.label: ≤60字
  - mustHavePoints.notes: ≤150字
  - keywords_any: ≤30字
- JSON救済処理: コードフェンス除去、外側オブジェクト抽出

### フィードバック
- 最大長: 400字
- トンチを利かせたユーモアに富んだ文章
- 必須要点の充足状況を明確に伝える

## Bedrockトークン数設定

各Lambda関数でのBedrock呼び出し時のMaxTokens設定を最適化しています。日本語は1文字あたり約2〜4トークン消費するため、出力文字数に応じて適切なトークン数を設定しています。

### GetNextQuizFunction（クイズ生成）

**設定箇所**: `backend/template.yaml` の `QuizPrompt` リソース

**MaxTokens**: 3500
- **理由**: 複雑なJSON構造（title、body、rubric全体、tags）で約3,500トークン必要
- **出力内容**:
  - title: 最大80字（約240トークン）
  - body: 最大300字（約900トークン）
  - rubric全体: 約1,500〜2,000トークン
    - expectedAnswer: 最大400字（約1,200トークン）
    - mustHavePoints: 4〜6個（各label≤60字、notes≤150字）
    - niceToHavePoints: 0〜2個
    - commonWrongClaims: 0〜2個
    - scoringPolicy
  - sourceSummary: 最大250字（約750トークン）
  - tags配列: 約100トークン

**Temperature**: 0.15（多様性と品質のバランス）

**実装**:
```yaml
InferenceConfiguration:
  Text:
    MaxTokens: 3500
    Temperature: 0.15
```

### JudgeAnswerFunction（回答判定）

**設定箇所**: `backend/src/judge_answer/app.py` の `_call_bedrock_judge_json` 関数

**MaxTokens**: 3000
- **理由**: feedback（最大400字）+ nextHint（最大280字）+ JSON構造で約2,500トークン必要
- **出力内容**:
  - result: "correct" / "close" / "incorrect"（約5トークン）
  - score: 0.0〜1.0（約5トークン）
  - mustPointsMet: ["p1","p2","p3","p4"]（約20トークン）
  - missingMustPoints: []（約5トークン）
  - feedback: 最大400字（約1,200トークン）
  - nextHint: 最大280字（約840トークン）
  - JSON構造オーバーヘッド: 約200トークン
  - 安全マージン: 約20〜30%

**Temperature**: 0.05（判定の一貫性重視）

**実装**:
```python
inference_config = {
    "maxTokens": 3000,
    "temperature": 0.05
}
if system_param is None:
    return bedrock.converse_json(messages=messages, inferenceConfig=inference_config)
return bedrock.converse_json(messages=messages, system=system_param, inferenceConfig=inference_config)
```

### GenerateExampleAnswerFunction（模範解答生成）

**設定箇所**: `backend/src/generate_example_answer/app.py` の `_call_bedrock_generate_text` 関数

**MaxTokens**: 2000
- **理由**: 純粋なテキスト出力で最大400字（約1,200トークン）+ 安全マージン
- **出力内容**:
  - 模範回答テキスト: 最大400字（実際は200〜300字推奨）
  - 400字 × 3トークン = 約1,200トークン
  - 安全マージン: 約25〜50%

**Temperature**: 0.15（適度な多様性）

**実装**:
```python
inference_config = {
    "maxTokens": 2000,
    "temperature": 0.15
}
if system_param is None:
    raw = bedrock.converse(messages=messages, inferenceConfig=inference_config)
else:
    raw = bedrock.converse(messages=messages, system=system_param, inferenceConfig=inference_config)
```

### トークン数設定の考え方

1. **日本語のトークン消費量**: 1文字あたり約2〜4トークン（平均3トークンで計算）
2. **JSON構造のオーバーヘッド**: 約200〜300トークン
3. **安全マージン**: 出力が途中で切れないよう20〜50%の余裕を確保
4. **Temperature設定**:
   - 判定タスク（JudgeAnswer）: 0.05（一貫性重視）
   - 生成タスク（GetNextQuiz、GenerateExampleAnswer）: 0.15（多様性と品質のバランス）

### トークン数設定の効果

- **出力の安定性向上**: 途中で切れるリスクを大幅に削減
- **コスト最適化**: 必要十分なトークン数で無駄を削減
- **品質向上**: 完全な出力により、JSON解析エラーやフィードバック不足を防止

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
| `backend/src/start_quiz_generation/app.py` |   6 | **非同期クイズ生成開始API**。GetNextQuizFunctionを非同期呼び出し、即座に202レスポンス | NextQuiz (Async) |
| `backend/src/get_next_quiz/app.py`         |   7 | **出題API本体**。MCP検索 → Bedrock生成 → クイズ状態保存 → レスポンス | NextQuiz         |
| `backend/src/judge_answer/app.py`          |   8 | **採点API本体**。入力正規化 → MCP検索 → Bedrock判定 → 検証 → 保存 | Judge            |
| `backend/src/generate_example_answer/app.py` |   9 | **回答例生成API**。問題とrubricを取得 → Bedrock生成 → 模範回答返却 | Example Answer   |
| `backend/src/get_current_quiz/app.py`      |  10 | **現在出題中クイズ取得API**。Host/Team 両方から参照される           | Sync             |
| `backend/src/get_scores/app.py`            |  11 | **スコア一覧取得API**。全チームのスコアを集計・返却                   | Sync             |
| `backend/src/submit_score/app.py`          |  12 | **スコア送信API**。初回回答時のみスコア加算、再評価時は履歴のみ更新         | Judge            |
| `backend/src/get_quiz_by_id/app.py`        |  13 | **過去問取得API**。questionIdを指定して過去のクイズを取得           | Review           |
| `backend/layers/common/python/common/config.py`     |  14 | 環境変数・定数定義（テーブル名、モデルID、閾値など）                     | 全体               |
| `backend/layers/common/python/common/schema.py`     |  15 | 入出力スキーマ定義（quiz / answer / result / rubric 等）    | NextQuiz / Judge |
| `backend/layers/common/python/common/normalize.py`  |  16 | ユーザー入力の正規化（空白、改行、表記揺れ補正）                        | Judge            |
| `backend/layers/common/python/common/validate.py`   |  17 | 採点結果の整合性検証（スコア範囲、mustPointsMet 比率など）            | Judge            |
| `backend/layers/common/python/common/ddb.py`        |  18 | DynamoDB アクセス層（クイズ状態・履歴・スコアの CRUD）              | 全体               |
| `backend/layers/common/python/common/bedrock.py`    |  19 | Bedrock 実行ラッパー（プロンプト構築、推論、レスポンス整形）              | NextQuiz / Judge |
| `backend/layers/common/python/common/mcp.py`        |  20 | AWS Knowledge MCP Server 呼び出し補助（検索・取得処理）        | NextQuiz / Judge |
| `backend/layers/common/python/common/errors.py`     |  21 | API 用例外・エラー整形（HTTP ステータス・メッセージ）                 | 全体               |
| `backend/layers/common/python/common/__init__.py`   |  22 | common パッケージ初期化                                 | 全体               |
| `backend/src/start_quiz_generation/__init__.py` |  23 | start_quiz_generation パッケージ初期化                  | NextQuiz (Async) |
| `backend/src/get_next_quiz/__init__.py`    |  24 | get_next_quiz パッケージ初期化                          | NextQuiz         |
| `backend/src/judge_answer/__init__.py`     |  25 | judge_answer パッケージ初期化                           | Judge            |
| `backend/src/generate_example_answer/__init__.py` |  26 | generate_example_answer パッケージ初期化                | Example Answer   |
| `backend/src/get_current_quiz/__init__.py` |  27 | get_current_quiz パッケージ初期化                       | Sync             |
| `backend/src/get_scores/__init__.py`       |  28 | get_scores パッケージ初期化                             | Sync             |
| `backend/src/submit_score/__init__.py`     |  29 | submit_score パッケージ初期化                           | Judge            |
| `backend/src/get_quiz_by_id/__init__.py`   |  30 | get_quiz_by_id パッケージ初期化                         | Review           |
