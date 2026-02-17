# aws_knowledge_quiz

## 概要

AWS SAM + Python + バニラJavaScriptで構築されたSPAのAWSクイズ出題アプリケーションです。

**主な機能**:
- AWS Knowledge MCP Serverを情報源としたクイズ自動生成
- Amazon Bedrock（Claude Sonnet）による採点とフィードバック
- rubric（採点基準）ベースの詳細な評価システム
- リアルタイムスコアボード（複数チーム対応）
- 重複回避機能（カーソルベースのクエリローテーション）

**技術スタック**:
- バックエンド: AWS SAM, Python 3.14, Lambda, DynamoDB, Bedrock, API Gateway
- フロントエンド: S3 + CloudFront, バニラJavaScript（SPA）
- AI/ML: Amazon Bedrock（Claude Sonnet 4.5）, AWS Knowledge MCP Server
- セキュリティ: Lambda Authorizer, CloudFrontカスタムヘッダー認証

## クイズ生成の仕組み

このアプリケーションは、AWS Knowledge MCP ServerとAmazon Bedrockを組み合わせて、多様なAWSクイズを自動生成します。

**クイズ生成可能数**: 理論上 **11,460通り** の異なるクイズが生成可能
- 1,910通りの異なるMCP検索クエリ × 6種類の出題スタイル

**重複回避の仕組み**:
1. カーソルベースのクエリローテーション（決定的な順序で全クエリを使用）
2. ハッシュ値による完全一致の重複チェック
3. 最近20問のヒントをBedrockに渡して類似問題を回避

詳細は [クイズ生成の仕組み](./docs/quiz.md) を参照してください。

### 採点基準のカスタマイズ

ファイル: `backend/src/judge_answer/app.py`

**rubric（採点基準）の調整**:
- **mustHavePoints**: 必須要点の数を変更（デフォルト: 4個）
- **scoringPolicy**: 閾値を調整
  - correct_threshold: 正解判定の閾値（デフォルト: 1.0）
  - close_threshold: 惜しい判定の閾値（デフォルト: 0.8）
  - correct_if_must_points_met_at_least: 正解に必要な必須要点数（デフォルト: 4）
  - close_if_must_points_met_at_least: 惜しいに必要な必須要点数（デフォルト: 3）

**フィードバックの調整**:
- 最大長: 400字（FEEDBACK_MAX_LENGTHで変更可能）
- トーン: トンチを利かせたユーモアに富んだ文章（プロンプトで調整）

## Bedrockのコンフィグ

### クイズ生成（GetNextQuizFunction）
- **モデル**: jp.anthropic.claude-sonnet-4-5-20250929-v1:0
- **MaxTokens**: 3500（複雑なJSON構造に対応）
- **Temperature**: 0.15（多様性と品質のバランス）
- **タイムアウト**: 接続5秒、読み取り25秒、リトライ1回
- **プロンプト管理**: Bedrock Prompt Management使用
- **プロンプト変数**:
  - category: カテゴリ（security、networking等）
  - level: 難易度（100=基礎、200=設計、300=実装、400=専門家）
  - avoid_duplicate_hint: 最近15問のヒント（タイトル、タグ、論点）
  - question_style: 出題スタイル
  - style_guidance: スタイル別の出題方針
  - source_context: MCP検索結果（最大2200文字）

### 回答採点（JudgeAnswerFunction）
- **モデル**: jp.anthropic.claude-sonnet-4-5-20250929-v1:0
- **MaxTokens**: 3000（feedback + nextHint + JSON構造）
- **Temperature**: 0.05（判定の一貫性重視）
- **フィードバック最大長**: 400字
- **採点基準**: rubric（mustHavePoints、niceToHavePoints、commonWrongClaims、scoringPolicy）
- **プロンプト構成**:
  - SYSTEM_PROMPT: 採点者の役割とルール
  - USER_PROMPT: 問題本文、rubric、ユーザー回答
- **出力形式**: JSON（result、score、mustPointsMet、missingMustPoints、feedback、nextHint）

### 模範解答生成（GenerateExampleAnswerFunction）
- **モデル**: jp.anthropic.claude-sonnet-4-5-20250929-v1:0
- **MaxTokens**: 2000（模範回答テキスト400字 + 安全マージン）
- **Temperature**: 0.15（適度な多様性）
- **出力**: 100点満点の模範回答（200〜300字推奨）

### Guardrail設定（オプション）
- **BedrockGuardrailIdentifier**: Guardrail ID（不適切なコンテンツをブロック）
- **BedrockGuardrailVersion**: Guardrail Version（デフォルト: DRAFT）
- ブロック時は適切なエラーメッセージを返却

### JSON出力の安定化
- **文字数制約**:
  - title: ≤80字
  - body: ≤300字
  - expectedAnswer: ≤400字
  - sourceSummary: ≤250字
  - mustHavePoints.label: ≤60字
  - mustHavePoints.notes: ≤150字
  - keywords_any: ≤30字
- **JSON救済処理**: コードフェンス除去、外側オブジェクト抽出

## パフォーマンスチューニング

### タイムアウト対策
- **Lambda実行時間**: 60秒
- **Lambdaメモリ**: 512MB（GetNextQuizFunction）
- **Bedrock接続タイムアウト**: 5秒
- **Bedrock読み取りタイムアウト**: 25秒
- **残り時間チェック**: 35秒未満で生成ループを停止
- **効果**: Lambda実行時間内でクイズ生成を完了、タイムアウトエラーを回避

### クイズ生成の多様性向上
- **MAX_MCP_REFRESH**: 0（デフォルト、環境変数で変更可能）
- **Temperature**: 0.15（出力の多様性向上）
- **カーソルベースのクエリローテーション**: 決定的な組み合わせ生成で重複回避
- **重複回避ヒント**: 最近20問（DUPLICATE_HINT_WINDOW: 20）のタイトル、タグ、論点を参照
- **効果**: 同じクイズの繰り返しを大幅に削減、多様な問題を生成

### JSON出力の安定化
- **MaxTokens**: 3500（複雑なJSON構造に十分対応）
- **文字数制約**: body≤300字、expectedAnswer≤400字、sourceSummary≤250字
- **JSON救済処理**: コードフェンス除去、外側オブジェクト抽出
- **プロンプト改善**: 「必ず完全なJSONを出力」を強調
- **効果**: JSON解析エラーを大幅に削減、安定したクイズ生成

### フィードバック長の調整
- **feedback最大長**: 280字→400字
- **効果**: より詳細で有益なフィードバックを提供、学習効果の向上

### MCP検索の最適化
- **SOURCE_SNIPPETS_MAX**: 3（取得するスニペット数）
- **SOURCE_CONTEXT_MAX_CHARS**: 2200（合計文字数制限）
- **効果**: 適切な情報量でクイズ生成、Bedrockのトークン消費を最適化

### 重複チェックの強化
- **ハッシュ値生成**: タイトル、本文、必須要点から生成
- **条件付き書き込み**: DynamoDBのConditionExpressionで重複を防止
- **効果**: 完全に同一のクイズが保存されることを防止

## その他ドキュメント

| ドキュメント名 | ファイル | 備考 |
| --- | --- | --- |
| デプロイ方法 | [deploy.md](./docs/deploy.md) | デプロイ手順とパラメータ | 
| システムアーキテクチャ | [architecture.md](./docs/architecture.md) | 構成図とシーケンス図 |
| クイズ生成の仕組み | [quiz.md](./docs/quiz.md) | クイズ生成アルゴリズムと重複回避 |
| フロントエンド | [frontend.md](./docs/frontend.md) | フロントエンドの説明 |
| バックエンド | [backend.md](./docs/backend.md) | バックエンド（Lambda)の説明 |
| 主なロジック | [logic.md](./docs/logic.md) | 採点アルゴリズムとデータフロー |
| UIが最新のクイズを取得する仕組み | [get_current_quiz.md](./docs/get_current_quiz.md) | ポーリングの詳細 |
