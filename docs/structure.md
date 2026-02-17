### システム構成図

> ※ 本図は処理全体の俯瞰を目的としており、

> クイズ状態・スコアの同期（GET /quiz/current, /scores）は

> ポーリングとして簡略表現しています。

> 詳細な処理順は以下のシーケンス図を参照してください。

### 1) エッジ＋API入口

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

### 2) 出題フロー（NextQuiz）

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

### 3) 採点＋同期（Judge / Sync）

```mermaid

flowchart LR

  FET[Team UI] -->|10 POST /quiz/judge| GW[API Gateway]

  GW -->|11 Invoke| JA[judge_answer]


  subgraph Core["AI / Data"]

    MCP[AWS Knowledge MCP]

    BR[Bedrock]

    DDB[DynamoDB]

  end


  JA -->|12 Load| DDB

  JA -->|13 Knowledge| MCP

  JA -->|14 Judge| BR

  JA -->|15 Save| DDB

  JA -->|16 Result| GW

  GW -->|17 Response| FET


  FEH[Host UI] -.->|18 GET /quiz/current| GW

  FET -.->|18 GET /quiz/current| GW

  GW -->|19 Invoke| GC[get_current_quiz]

  GC --> DDB


  FEH -.->|18 GET /scores| GW

  FET -.->|18 GET /scores| GW

  GW -->|20 Invoke| GS[get_scores]

  GS --> DDB

```

### シーケンス図

- 「次のクイズ」ボタンを押したとき

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

  FEH->>GW: GET /quiz/generate (構成図:2)

  GW->>SQG: Invoke (構成図:3)

  SQG->>NQ: Async Invoke (構成図:4)

  SQG-->>GW: 202 Accepted (構成図:5)

  GW-->>FEH: 202 Accepted (構成図:6)


  Note over NQ,DB: 非同期でクイズ生成実行


  NQ->>MCP: ナレッジ検索 (構成図:7)

  MCP-->>NQ: ナレッジ要点 (構成図:7)


  NQ->>BR: ナレッジ + 条件でクイズ生成 (構成図:8)

  BR-->>NQ: クイズ（問題/選択肢/正解/根拠）(構成図:8)


  NQ->>DB: currentQuiz / 状態保存 (構成図:9)

  DB-->>NQ: OK (構成図:9)


  Note over FEH,DB: Host UIは定期ポーリングで最新クイズを取得

```

- 「採点する」ボタンを押したとき

```mermaid

sequenceDiagram

  autonumber

  actor Team as Team(Browser)

  participant FET as Team UI

  participant GW as API Gateway

  participant JA as judge_answer

  participant DB as DynamoDB

  participant MCP as Knowledge MCP

  participant BR as Bedrock


  Team->>FET: 回答入力・送信

  FET->>GW: POST /quiz/judge (構成図:10)

  GW->>JA: Invoke (構成図:11)


  JA->>DB: クイズ情報取得 (構成図:12)

  DB-->>JA: quiz context (構成図:12)


  JA->>MCP: 根拠ナレッジ取得 (構成図:13)

  MCP-->>JA: ナレッジ要点 (構成図:13)


  JA->>BR: 回答判定依頼（rubric）(構成図:14)

  BR-->>JA: score / feedback (構成図:14)


  JA->>DB: スコア・履歴保存 (構成図:15)

  DB-->>JA: OK (構成図:15)


  JA-->>GW: result JSON (構成図:16)

  GW-->>FET: result JSON (構成図:17)

```

- クイズの表示同期（全クライアント）

```mermaid

sequenceDiagram

  autonumber

  participant FE as Frontend

  participant GW as API Gateway

  participant GC as get_current_quiz

  participant GS as get_scores

  participant DB as DynamoDB


  loop 定期ポーリング（現在クイズ）

    FE->>GW: GET /quiz/current (構成図:18)

    GW->>GC: Invoke (構成図:19)

    GC->>DB: Get currentQuiz

    DB-->>GC: quiz

    GC-->>GW: quiz

    GW-->>FE: quiz

  end


  loop 定期ポーリング（スコア）

    FE->>GW: GET /scores (構成図:18)

    GW->>GS: Invoke (構成図:20)

    GS->>DB: Get scores

    DB-->>GS: scores

    GS-->>GW: scores

    GW-->>FE: scores

  end

```
