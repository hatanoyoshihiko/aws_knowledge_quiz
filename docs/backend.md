# Lambda関数の処理概要

## GetNextQuizFunction（クイズ生成）

**役割**: 新しいクイズを生成して出題

**処理フロー**:

1.**MCP検索クエリの決定的生成**

- カテゴリ（security、networking、storage、serverless、well-architected）ごとに定義された部品（services、topics、angles）から組み合わせを生成
- カーソルベースでインデックスを管理し、同じクエリの繰り返しを回避
- クエリ空間サイズ = services数 × topics数 × angles数

2.**AWS Knowledge MCP Serverからの情報取得**

- 生成されたクエリでMCP検索を実行
- 最大3件のスニペットを取得（SOURCE_SNIPPETS_MAX: 3）
- 合計2200文字以内に制限（SOURCE_CONTEXT_MAX_CHARS: 2200）

3.**Bedrock Prompt Managementでクイズ生成**

- プロンプト変数: category、level、avoid_duplicate_hint（最近15問）、question_style、style_guidance、source_context
- MaxTokens: 3500、Temperature: 0.15
- JSON形式で出力（コードフェンス除去処理あり）

4.**重複チェックと保存**

- タイトル、本文、必須要点からハッシュ値を生成
- DynamoDBに条件付き書き込み（重複時は失敗）
- 成功時はカーソルを進める（次回は異なるクエリを使用）

5.**リトライとタイムアウト**

- MAX_MCP_REFRESH: 0（デフォルト、環境変数で変更可能）
- 残り時間35秒未満で生成ループを停止
- 重複時は503エラーを返し、クライアント側で再試行を促す

**パフォーマンス設定**:

- Lambda実行時間: 60秒、メモリ: 512MB
- Bedrock接続タイムアウト: 5秒、読み取りタイムアウト: 25秒
- MaxTokens: 3500、Temperature: 0.15

## JudgeAnswerFunction（回答採点）

**役割**: ユーザーの回答を採点してフィードバックを提供

**処理フロー**:

1.**問題情報の取得**

- questionIdでDynamoDBから問題とrubric（採点基準）を取得

2.**Bedrockでの採点**

- SYSTEM_PROMPT: 採点者の役割とルールを定義
- USER_PROMPT: 問題本文、rubric、ユーザー回答を含む
- MaxTokens: 3000、Temperature: 0.05（判定の一貫性重視）
- 出力: result、score、mustPointsMet、missingMustPoints、feedback、nextHint

3.**採点結果の検証**

- mustPointsMetとmissingMustPointsの整合性チェック
- スコアの範囲チェック（0.0〜1.0）
- scoringPolicyとの整合性確認

4.**スコア反映**

- 初回回答: ScoresTableにスコアを加算
- 再評価: AnswerHistoryTableのみ更新（スコアは変更しない）
- 回答履歴に詳細情報を記録（result、score、feedback、試行回数）

**rubric（採点基準）の構造**:

-**mustHavePoints**: 必須要点（通常4個）- 正解判定に必須

-**niceToHavePoints**: 加点要素（0〜1個）- より深い理解を示す内容

-**commonWrongClaims**: よくある誤解（0〜1個）- 含まれると減点

-**scoringPolicy**: 採点ポリシー（correct_threshold: 1.0、close_threshold: 0.8）

## GetCurrentQuizFunction（現在クイズ取得）

**役割**: 現在出題中のクイズを取得

**処理フロー**:

- DynamoDB GSI_Recentから最新1件を取得
- 問題情報（タイトル、本文、カテゴリ、レベル）を返却

**用途**: Host/Team両UIから15秒ごとにポーリング

## GetScoresFunction（スコアボード取得）

**役割**: 全チームのスコアボードを取得

**処理フロー**:

- ScoresTableをScanして全チームのスコアを取得
- スコア降順、更新日時降順でソート
- チーム名、スコア、更新日時を返却

## StartQuizGenerationFunction（クイズ生成開始）

**役割**: クイズ生成を非同期で開始

**処理フロー**:

- GetNextQuizFunctionを非同期で呼び出し
- 即座にレスポンスを返却（生成完了を待たない）

## SubmitScoreFunction（スコア送信）

**役割**: 採点結果をスコアボードに反映

**処理フロー**:

- AnswerHistoryTableで回答履歴を確認（初回か再評価か判定）
- 初回回答の場合のみScoresTableにスコアを加算
- 再評価の場合は履歴のみ更新（スコアは変更しない）

## GetQuizByIdFunction（過去問取得）

**役割**: questionIdを指定して過去のクイズを取得

**処理フロー**:

- questionIdをキーにDynamoDBから問題を取得
- 復習機能や過去問参照に使用

## GenerateExampleAnswerFunction（回答例生成）

**役割**: 100点満点の回答例を生成

**処理フロー**:

1.**問題情報の取得**

- questionIdでDynamoDBから問題とrubric（採点基準）を取得

2.**Bedrockで回答例生成**

- 問題本文、rubric（必須要点、加点要素）を含むプロンプトを構築
- MaxTokens: 2000、Temperature: 0.15
- 100点を満たす模範回答を生成（200〜300字程度）
- 必須要点をすべて含み、加点要素も考慮した回答

3.**回答例の返却**

- 生成された回答例をJSON形式で返却
- Team UIで「回答例を生成」ボタン押下後に表示

**パフォーマンス設定**:

- Lambda実行時間: 60秒、メモリ: 512MB
- Bedrock接続タイムアウト: 5秒、読み取りタイムアウト: 25秒
- MaxTokens: 2000、Temperature: 0.15
