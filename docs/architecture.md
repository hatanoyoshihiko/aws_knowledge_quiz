# システムアーキテクチャ

AWSクイズアプリケーションのシステムアーキテクチャを図で説明。

> **注意**: 本図は処理全体の俯瞰を目的とし、クイズ状態・スコアの同期（GET /quiz/current, /scores）はポーリングとして簡略表現。詳細な処理順は各シーケンス図を参照。

## 1) エッジ＋API入口

```mermaid

flowchart LR

  subgraph Edge["Edge / Frontend"]

    User[User Browser] -->|0 Access| CF[CloudFront]

    CF -->|1 UI| FEH[Host UI<br/>host.html]

    CF -->|1' UI| FET[Team UI<br/>team.html]

  end


  subgraph API["API Gateway"]

    GW[API Gateway<br/>HTTP API]

  end


  FEH -->|2 GET /quiz/generate| GW

  FET -->|9 POST /quiz/judge| GW

  FEH -.->|17 GET /quiz/current, /scores| GW

  FET -.->|17 GET /quiz/current, /scores| GW

```

## 2) 出題フロー（NextQuiz）

```mermaid

flowchart LR

  FEH[Host UI] -->|2 GET /quiz/generate| GW[API Gateway]

  GW -->|3 Invoke| SQG[start_quiz_generation]

  SQG -->|4 Async Invoke| NQ[get_next_quiz]

  SQG -->|5 202 Accepted| GW

  GW -->|6 Response| FEH


  subgraph Core["AI / Data"]

    MCP[AWS Knowledge MCP]

    BR[Bedrock]

    DDB[DynamoDB]

  end


  NQ -->|7 Knowledge| MCP

  NQ -->|8 Generate| BR

  NQ -->|9 Save| DDB

```

## 3) 採点＋同期（Judge / Sync）

```mermaid

flowchart LR

  FET[Team UI] -->|10 POST /quiz/answer| GW[API Gateway]

  GW -->|11 Invoke| JA[judge_answer]


  subgraph Core["AI / Data"]

    BR[Bedrock]

    DDB[DynamoDB]

  end


  JA -->|12 Load Quiz & Rubric| DDB

  JA -->|13 Judge with Rubric| BR

  JA -->|14 Save Result| DDB

  JA -->|15 Result| GW

  GW -->|16 Response| FET


  FEH[Host UI] -.->|17 GET /quiz/current| GW

  FET -.->|17 GET /quiz/current| GW

  GW -->|18 Invoke| GC[get_current_quiz]

  GC --> DDB


  FEH -.->|17 GET /scores| GW

  FET -.->|17 GET /scores| GW

  GW -->|19 Invoke| GS[get_scores]

  GS --> DDB

```

## 4) 回答例生成（Example Answer）

```mermaid

flowchart LR

  FET[Team UI] -->|20 GET /quiz/example-answer| GW[API Gateway]

  GW -->|21 Invoke| GEA[generate_example_answer]


  subgraph Core["AI / Data"]

    BR[Bedrock]

    DDB[DynamoDB]

  end


  GEA -->|22 Load Quiz & Rubric| DDB

  GEA -->|23 Generate 100点 Answer| BR

  GEA -->|24 Example Answer| GW

  GW -->|25 Response| FET

```

## シーケンス図

### 1. クイズ生成フロー（非同期）

「次のクイズ」ボタンを押したときの処理フロー

```mermaid

sequenceDiagram

  autonumber

  actor Host as Host(Browser)

  participant FEH as Host UI

  participant GW as API Gateway

  participant SQG as start_quiz_generation

  participant NQ as get_next_quiz

  participant MCP as Knowledge MCP

  participant BR as Bedrock

  participant DB as DynamoDB


  Host->>FEH: 「次のクイズ」クリック

  FEH->>GW: GET /quiz/generate?category=security&level=200

  GW->>SQG: Invoke

  SQG->>NQ: Async Invoke（非同期）

  SQG-->>GW: 202 Accepted

  GW-->>FEH: 202 Accepted（即座に返却）


  Note over FEH: ポーリング開始（5秒ごと）


  Note over NQ,DB: 以下、バックグラウンドで実行


  NQ->>DB: カーソル取得（category×level）

  DB-->>NQ: 次回使用インデックス（例: 42）


  NQ->>NQ: クエリ生成（idx=42）<br/>service×topic×angle


  NQ->>MCP: search_documentation(query)

  MCP-->>NQ: スニペット×3（最大2200文字）


  NQ->>DB: 最近20問のヒント取得

  DB-->>NQ: タイトル、タグ、論点


  NQ->>BR: Prompt Management<br/>+ source_context<br/>+ avoid_duplicate_hint

  BR-->>NQ: クイズJSON（3500トークン）


  NQ->>NQ: ハッシュ値生成<br/>（title+body+mustPoints）


  NQ->>DB: 条件付き書き込み<br/>（ハッシュ重複チェック）

  alt 書き込み成功

    DB-->>NQ: OK

    NQ->>DB: カーソル更新（idx=43）

    DB-->>NQ: OK

  else 重複エラー

    DB-->>NQ: ConditionalCheckFailedException

    Note over NQ: リトライまたは503エラー

  end


  Note over FEH: ポーリングで新しいクイズを検出

  FEH->>GW: GET /quiz/current

  GW->>DB: GSI_Recent（最新1件）

  DB-->>GW: 新しいクイズ

  GW-->>FEH: クイズ表示

```

### 2. 採点フロー

「採点する」ボタンを押したときの処理フロー

```mermaid

sequenceDiagram

  autonumber

  actor Team as Team(Browser)

  participant FET as Team UI

  participant GW as API Gateway

  participant JA as judge_answer

  participant DB as DynamoDB

  participant BR as Bedrock


  Team->>FET: 回答入力・送信

  FET->>GW: POST /quiz/answer<br/>{questionId, teamId, answer}

  GW->>JA: Invoke


  JA->>DB: クイズ＆Rubric取得<br/>（questionId）

  DB-->>JA: Question + Rubric<br/>（mustHavePoints, scoringPolicy）


  JA->>JA: 回答正規化<br/>（空白・改行除去）


  JA->>BR: 採点依頼<br/>SYSTEM: 採点ルール<br/>USER: Question + Rubric + Answer

  BR-->>JA: 採点結果JSON<br/>{result, score, mustPointsMet,<br/>feedback, nextHint}


  JA->>JA: 結果検証<br/>（mustPointsMet整合性、<br/>score範囲チェック）


  JA->>DB: 回答履歴保存<br/>AnswerHistoryTable

  DB-->>JA: OK


  alt 初回回答

    JA->>DB: スコア加算<br/>ScoresTable

    DB-->>JA: OK

  else 再評価

    Note over JA,DB: スコアは変更しない<br/>（履歴のみ更新）

  end


  JA-->>GW: result JSON

  GW-->>FET: 採点結果表示<br/>（スコア、フィードバック、要点詳細）

```

### 3. 回答例生成フロー

「回答例を生成」ボタンを押したときの処理フロー

```mermaid

sequenceDiagram

  autonumber

  actor Team as Team(Browser)

  participant FET as Team UI

  participant GW as API Gateway

  participant GEA as generate_example_answer

  participant DB as DynamoDB

  participant BR as Bedrock


  Team->>FET: 「回答例を生成」クリック

  FET->>GW: GET /quiz/example-answer?questionId=xxx

  GW->>GEA: Invoke


  GEA->>DB: クイズ＆Rubric取得<br/>（questionId）

  DB-->>GEA: Question + Rubric<br/>（mustHavePoints, niceToHavePoints）


  GEA->>BR: 模範回答生成依頼<br/>SYSTEM: 100点満点の回答を生成<br/>USER: Question + Rubric

  BR-->>GEA: 模範回答テキスト<br/>（400字以内）


  GEA->>GEA: 文字数検証<br/>（450字超過時は切り詰め）


  GEA-->>GW: {exampleAnswer, sourceUrls}

  GW-->>FET: 模範回答表示

```

### 4. クイズ同期フロー（ポーリング）

Host UIとTeam UIが最新クイズとスコアを取得する処理フロー

```mermaid

sequenceDiagram

  autonumber

  participant FE as Frontend<br/>(Host/Team UI)

  participant GW as API Gateway

  participant GC as get_current_quiz

  participant GS as get_scores

  participant DB as DynamoDB


  Note over FE: 15秒ごとに自動実行


  loop 定期ポーリング（現在クイズ）

    FE->>GW: GET /quiz/current

    GW->>GC: Invoke

    GC->>DB: GSI_Recent Query<br/>（GSI1PK="RECENT", Limit=1,<br/>ScanIndexForward=False）

    DB-->>GC: 最新クイズ1件

    GC-->>GW: {question: {...}}

    GW-->>FE: クイズ表示更新


    alt questionIdが変わった場合

      Note over FE: 新しいクイズを画面に表示

    else questionIdが同じ場合

      Note over FE: 何もしない

    end

  end


  loop 定期ポーリング（スコアボード）

    FE->>GW: GET /scores

    GW->>GS: Invoke

    GS->>DB: Scan ScoresTable

    DB-->>GS: 全チームのスコア

    GS->>GS: スコア降順ソート

    GS-->>GW: [{teamId, score, updatedAt}]

    GW-->>FE: スコアボード更新

  end

```

## 重複回避の仕組み

クイズ生成時の重複回避は3段階で実施：

1. **カーソルベースのクエリローテーション**: 決定的な順序で全クエリを使用
2. **ハッシュ値による重複チェック**: DynamoDB条件付き書き込みで完全一致を排除
3. **最近20問のヒント**: Bedrockに渡して類似問題を回避

詳細は [クイズ生成の仕組み](./quiz.md#重複回避の仕組み) を参照。
