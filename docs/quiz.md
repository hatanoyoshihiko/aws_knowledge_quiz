# クイズ生成の仕組み

AWSクイズアプリケーションのクイズ生成メカニズムを詳しく説明。

## 概要

クイズ生成は以下の3つの主要コンポーネントで構成：

1. **MCP検索クエリの決定的生成**: カーソルベースのローテーションで多様なクエリを生成
2. **AWS Knowledge MCP Server**: AWS公式ドキュメントから情報を取得
3. **Amazon Bedrock**: Claude Sonnetを使用してクイズを生成

## クイズ生成可能数

### カテゴリ別のクエリ空間

QUERY_COMPONENTSで定義された要素から、決定的な組み合わせを生成。

**表の各列の意味**:
- **services**: AWSサービス名のリスト（例: IAM, S3, Lambda）
- **topics**: トピック・機能のリスト（例: 最小権限, 暗号化, 監査）
- **angles**: 観点・切り口のリスト（例: ベストプラクティス, 設計, 運用）
- **クエリ数**: services × topics × angles の組み合わせ総数

**計算例（security）**:
```
10種類のサービス × 9種類のトピック × 5種類の観点 = 450通りのMCP検索クエリ

具体例:
- クエリ1: "IAM 最小権限 ベストプラクティス"
- クエリ2: "IAM 最小権限 誤解"
- クエリ3: "IAM 最小権限 設計"
- ...
- クエリ450: "Secrets Manager アクセス制御 トレードオフ"
```

| カテゴリ | services | topics | angles | クエリ数 |
| --- | ---: | ---: | ---: | ---: |
| security | 10 | 9 | 5 | 450 |
| networking | 12 | 8 | 5 | 480 |
| storage | 8 | 8 | 5 | 320 |
| serverless | 10 | 9 | 5 | 450 |
| well-architected | 7 | 6 | 5 | 210 |
| **合計** | - | - | - | **1,910** |

**重要**: これらのクエリはMCP検索に使用されるもので、クエリ1つにつき1つのクイズが生成されるわけではありません。同じクエリでも、Bedrockの生成により異なるクイズが作成される可能性があります。

### 出題スタイルとの組み合わせ

QUESTION_STYLESが6種類あるため、理論上は以下の通り：

```
1,910通りのMCP検索クエリ × 6種類の出題スタイル = 11,460通りの異なるクイズ
```

**計算の意味**:
- 同じMCP検索クエリでも、出題スタイルが異なれば異なる問題になる
- 例: "IAM 最小権限 ベストプラクティス" というクエリから
  - 「使い分け」スタイル: IAMポリシーの種類を選択する問題
  - 「トレードオフ」スタイル: 最小権限と運用効率のバランスを問う問題
  - 「誤り探し」スタイル: 誤ったIAMポリシー設定を見つける問題
  - など、6通りの異なる問題が生成可能

**QUESTION_STYLES（出題スタイル）**:
- 使い分け: 複数の選択肢から最適なものを選ぶ問題
- トレードオフ: メリット・デメリットを考慮する問題
- 誤り探し: 誤った記述を見つける問題
- 運用・障害対応: 実際の運用シナリオに基づく問題
- 監査・コンプラ観点: セキュリティやコンプライアンスに関する問題
- コスト最適化: コスト削減や最適化に関する問題

**注意**: 11,460通りは理論上の最大値。実際には以下の要因により、生成されるクイズの種類はさらに多様：
- Bedrockの生成による多様性（Temperature: 0.15）
- MCP検索結果の違い（同じクエリでも時期により異なるドキュメントが取得される可能性）
- 重複回避ヒント（最近20問の情報により、類似問題を避ける）

## クイズ生成フロー

### 1. 非同期クイズ生成の開始

**関数**: StartQuizGenerationFunction

Host UIで「次のクイズ」ボタンを押すと、以下の処理が実行：

```
1. Host UI → API Gateway: GET /quiz/generate?category=security&level=200
2. API Gateway → StartQuizGenerationFunction: Invoke
3. StartQuizGenerationFunction → GetNextQuizFunction: Async Invoke（非同期）
4. StartQuizGenerationFunction → Host UI: 202 Accepted（即座に返却）
5. Host UI: ポーリング開始（5秒ごと、最大12回）
```

**利点**:
- UIのブロッキングを回避（30秒待たずに即座にレスポンス）
- クイズ生成中も他の操作が可能
- タイムアウトリスクの軽減

### 2. クイズ生成の実行

**関数**: GetNextQuizFunction

バックグラウンドで以下の処理が実行：

#### ステップ1: カーソル取得

```python
# DynamoDBからカテゴリ×レベルごとのカーソルを取得
# 例: security×200 → カーソル=42
cursor_pk = f"CURSOR#{category}#{level_bucket}"
cursor_item = table.get_item(Key={"PK": cursor_pk})
next_idx = cursor_item.get("NextIdx", 0)  # 例: 42
```

#### ステップ2: MCP検索クエリの生成

```python
# クエリ空間サイズを計算
query_space_size = len(services) × len(topics) × len(angles)
# security: 10 × 9 × 5 = 450

# インデックスから部品を選択
idx = 42
service_idx = idx // (len(topics) × len(angles))  # 42 // 45 = 0
topic_idx = (idx // len(angles)) % len(topics)    # (42 // 5) % 9 = 8
angle_idx = idx % len(angles)                     # 42 % 5 = 2

# クエリを構築
service = services[service_idx]  # services[0]
topic = topics[topic_idx]        # topics[8]
angle = angles[angle_idx]        # angles[2]

query = f"{service} {topic} {angle}"
# 例: "IAM 監査 設計"
```

#### ステップ3: MCP検索

```python
# AWS Knowledge MCP Serverから情報を取得
snippets = mcp_client.search(
    query=query,
    max_snippets=3  # SOURCE_SNIPPETS_MAX
)

# スニペットを結合（最大1500文字）
source_context = build_source_context(snippets)
# SOURCE_CONTEXT_MAX_CHARS: 1500
```

#### ステップ4: 重複回避ヒントの取得

```python
# 最近15問のヒントを取得
recent_hints = get_recent_hints(limit=15)  # DUPLICATE_HINT_WINDOW

# ヒント文字列を構築
avoid_hint = "\n".join([
    f"- {h['title']} (tags: {h['tags']})"
    for h in recent_hints
])
```

#### ステップ5: Bedrock Prompt Managementでクイズ生成

```python
# プロンプト変数を設定
variables = {
    "category": category,
    "level": level,
    "avoid_duplicate_hint": avoid_hint,
    "question_style": question_style,
    "style_guidance": style_guidance,
    "source_context": source_context
}

# Bedrockでクイズ生成
quiz_json = bedrock.converse_prompt_json(
    prompt_arn=BEDROCK_PROMPT_ARN,
    variables=variables,
    inferenceConfig={
        "maxTokens": 3500,
        "temperature": 0.15
    }
)
```

**生成されるJSON構造**:
```json
{
  "title": "IAMポリシーの評価順序",
  "body": "以下のシナリオで...",
  "category": "security",
  "level": 200,
  "rubric": {
    "expectedAnswer": "...",
    "mustHavePoints": [
      {"id": "p1", "label": "...", "keywords_any": [...], "notes": "..."},
      {"id": "p2", "label": "...", "keywords_any": [...], "notes": "..."},
      {"id": "p3", "label": "...", "keywords_any": [...], "notes": "..."},
      {"id": "p4", "label": "...", "keywords_any": [...], "notes": "..."}
    ],
    "niceToHavePoints": [...],
    "commonWrongClaims": [...],
    "scoringPolicy": {
      "correct_threshold": 1.0,
      "close_threshold": 0.8,
      "must_points_total": 4,
      "close_if_must_points_met_at_least": 3,
      "correct_if_must_points_met_at_least": 4
    }
  },
  "sourceSummary": "...",
  "tags": ["iam", "policy", "evaluation"]
}
```

#### ステップ6: ハッシュ値生成と重複チェック

```python
# ハッシュ値を生成
hash_input = canonical_question_string(
    title=quiz["title"],
    body=quiz["body"],
    must_have_points=quiz["rubric"]["mustHavePoints"]
)
question_hash = sha256_hex(hash_input)

# DynamoDBに条件付き書き込み
item = {
    "QuestionHash": question_hash,  # Primary Key
    "GSI1PK": "RECENT",
    "GSI1SK": created_at,
    "Question": quiz,
    "Rubric": quiz["rubric"],
    "Tags": quiz["tags"],
    "CreatedAt": created_at
}

try:
    table.put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(QuestionHash)"
    )
except ConditionalCheckFailedException:
    # 重複エラー: リトライまたは503エラー
    raise DuplicateError("Quiz already exists")
```

#### ステップ7: カーソル更新

```python
# 成功時はカーソルを進める
next_idx = (next_idx + 1) % query_space_size
# 例: 42 → 43（450通りを超えたら0に戻る）

table.put_item(
    Item={
        "PK": cursor_pk,
        "NextIdx": next_idx,
        "UpdatedAt": datetime.now().isoformat()
    }
)
```

### 3. クイズの同期

Host UIとTeam UIは15秒ごとにポーリングして最新クイズを取得：

```javascript
// 15秒ごとに実行
setInterval(async () => {
    const response = await fetch('/quiz/current');
    const data = await response.json();
    
    if (data.question && data.question.questionId !== currentQuizId) {
        // 新しいクイズを表示
        displayQuiz(data.question);
        currentQuizId = data.question.questionId;
    }
}, 15000);
```

## 重複回避の仕組み

クイズ生成時の重複回避は3段階で実施：

### 1段階目: カーソルベースのクエリローテーション

**目的**: 同じMCP検索クエリの繰り返しを回避

**仕組み**:
```
カテゴリ×レベルごとにカーソル（次回使用インデックス）を管理
例: security×200 → カーソル=42

クエリ空間サイズ = services数 × topics数 × angles数
security: 10 × 9 × 5 = 450通り

idx=42 の場合:
  service_idx = 42 // (9 × 5) = 0 → services[0]
  topic_idx = (42 // 5) % 9 = 8 → topics[8]
  angle_idx = 42 % 5 = 2 → angles[2]

クイズ生成成功時: カーソル=43 に更新
次回は異なるクエリを使用（0 → 1 → 2 → ... → 449 → 0）
```

**効果**: 
- 決定的な順序でクエリを生成
- 全450通りのクエリを均等に使用
- ランダム性による偏りを排除

### 2段階目: ハッシュ値による重複チェック

**目的**: 完全に同一のクイズの保存を防止

**仕組み**:
```
ハッシュ値 = SHA256(title + body + mustHavePoints)

DynamoDB条件付き書き込み:
  ConditionExpression: attribute_not_exists(QuestionHash)

重複時: ConditionalCheckFailedException
→ リトライまたは503エラー
```

**効果**:
- タイトル、本文、必須要点が完全に同一のクイズを排除
- DynamoDBのアトミック操作で確実に重複を防止

### 3段階目: 最近15問のヒント

**目的**: 類似問題の生成を回避

**仕組み**:
```
Bedrockに最近15問の情報を渡す:
  - タイトル
  - タグ

Bedrockが類似問題を避けるように生成
```

**ヒント例**:
```
最近の出題:
- IAMポリシーの評価順序 (tags: iam,policy)
- S3バケットポリシーの設定 (tags: s3,policy)
- ...（最大15問）

これらと異なる観点・論点のクイズを生成してください。
```

**効果**:
- LLMの文脈理解により、類似問題を自然に回避
- 完全一致ではないが似た問題の生成を抑制

## クイズ生成のカスタマイズ

### QUERY_COMPONENTSの編集

ファイル: `backend/src/get_next_quiz/app.py`

新しいサービスやトピックを追加する場合：

```python
QUERY_COMPONENTS: dict[str, dict[str, list[str]]] = {
    "security": {
        "services": [
            "IAM",
            "STS AssumeRole",
            # 新しいサービスを追加
            "Cognito",
            "Secrets Manager",
        ],
        "topics": [
            "最小権限",
            "評価ロジック",
            # 新しいトピックを追加
            "多要素認証",
        ],
        "angles": [
            "ベストプラクティス",
            "誤解",
            # 新しい観点を追加
            "コスト最適化",
        ],
    },
    # 他のカテゴリも同様
}
```

**注意**: 
- 部品を追加すると、クエリ空間サイズが増加します
- 例: services 10→11, topics 9→10 の場合: 450→550通り（+100通り）

### QUESTION_STYLESの編集

新しい出題スタイルを追加する場合：

```python
QUESTION_STYLES: list[tuple[str, str]] = [
    ("使い分け", "A/B/Cの違いを『要件→選定理由→注意点』で問う。"),
    ("トレードオフ", "条件次第で変わる論点を出す。何を捨て何を取るかを答えさせる。"),
    # 新しいスタイルを追加
    ("実装手順", "具体的な実装手順を順序立てて答えさせる。"),
]
```

### Bedrock Prompt Managementの調整

ファイル: `backend/template.yaml` の `QuizPrompt` リソース

プロンプトテンプレートを編集して、出題方針を変更可能：

```yaml
QuizPrompt:
  Type: AWS::Bedrock::Prompt
  Properties:
    Variants:
      - Name: "default"
        TemplateConfiguration:
          Chat:
            System:
              - Text: |
                  AWS教育用クイズ作成者。SOURCE_CONTEXTのみを根拠に、日本語で1問生成。
                  
                  【カスタマイズ例】
                  - 難易度を調整: level 100は初心者向け、400は専門家向け
                  - 文体を変更: ビジネス文書調、カジュアル調など
                  - 制約を追加: 特定のサービスを必ず含める、など
```

## パフォーマンス設定

### Lambda設定

| 項目 | 値 | 説明 |
| --- | --- | --- |
| タイムアウト | 60秒 | クイズ生成の最大実行時間 |
| メモリ | 512MB | 十分なメモリで安定動作 |
| 残り時間チェック | 35秒未満 | この時点で生成ループを停止 |

### Bedrock設定

| 項目 | 値 | 説明 |
| --- | --- | --- |
| MaxTokens | 3500 | 複雑なJSON構造に対応 |
| Temperature | 0.15 | 多様性と品質のバランス |
| 接続タイムアウト | 5秒 | 接続確立の最大待機時間 |
| 読み取りタイムアウト | 25秒 | レスポンス受信の最大待機時間 |

### MCP検索設定

| 項目 | 値 | 説明 |
| --- | --- | --- |
| SOURCE_SNIPPETS_MAX | 3 | 取得するスニペット数 |
| SOURCE_CONTEXT_MAX_CHARS | 1500 | 合計文字数制限 |
| SEARCH_TOP_K | 2 | search_documentationの上位件数 |
| READ_TOP_K | 2 | read_documentationの上位件数 |

### 重複回避設定

| 項目 | 値 | 説明 |
| --- | --- | --- |
| DUPLICATE_HINT_WINDOW | 15 | 最近何問のヒントを使用するか |
| MAX_ATTEMPTS | 1 | 同一MCP検索結果での再試行回数 |
| MAX_MCP_REFRESH | 0 | 異なるMCPクエリでの再試行回数 |

## トラブルシューティング

### クイズ生成が遅い

**原因**: MCP検索やBedrock生成に時間がかかっている

**対策**:
1. SOURCE_CONTEXT_MAX_CHARSを減らす（1500 → 1000）
2. SOURCE_SNIPPETS_MAXを減らす（3 → 2）
3. Lambdaメモリを増やす（512MB → 1024MB）

### 同じようなクイズが生成される

**原因**: カーソルが正しく更新されていない、またはヒントが不足

**対策**:
1. DUPLICATE_HINT_WINDOWを増やす（15 → 30）
2. QUERY_COMPONENTSに新しいサービス・トピックを追加
3. Temperatureを上げる（0.15 → 0.25）

### 重複エラーが頻発する

**原因**: クエリ空間が小さい、またはBedrockが似た出力を生成

**対策**:
1. QUERY_COMPONENTSを拡充してクエリ空間を増やす
2. MAX_MCP_REFRESHを増やす（0 → 2）
3. プロンプトテンプレートを調整して多様性を向上

### JSON解析エラーが発生する

**原因**: Bedrockの出力が不完全、または文字数制約を超過

**対策**:
1. MaxTokensを増やす（3500 → 4000）
2. 文字数制約を緩和（body≤300字 → ≤400字）
3. プロンプトで「必ず完全なJSONを出力」を強調

## 関連ドキュメント

- [システムアーキテクチャ](./architecture.md): クイズ生成フローの図解
- [バックエンド](./backend.md): Lambda関数の詳細
- [主なロジック](./logic.md): 全体的なロジックとアルゴリズム
