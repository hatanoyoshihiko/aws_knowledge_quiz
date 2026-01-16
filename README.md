# aws_knowledge_quiz

## 概要

SPAのAWSクイズ出題アプリケーションです。
クイズ出題と回答と採点を行います。
回答に対して、フィードバックを行います。

出題はAWS Knowledge MCP Serverを情報源とし、Amazon Bedrockでクイズを生成します。
回答に対する採点も同MCP Serverを情報源とし、Amazon Bedrockで判定、フードバックを行います。

## デプロイ方法

### 前準備

- リポジトリのクローン

```bash
git clone https://github.com/hatanoyoshihiko/aws_knowledge_quiz.git
```

- 変数定義

```bash
export AWS_REGION=ap-northeast-1
export FRONTEND_STACK_NAME=aws-knowledge-quiz-frontend
export BACKEND_STACK_NAME=aws-knowledge-quiz-backend
export AWS_PROFILE=YOUR_PROFILE
export API_HEADER_VALUE=1234567890qwertyuiop@
```

### バックエンドのデプロイ

- CloudFrontのURLを変数として格納する

```bash
CLOUDFRONT_URL="$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$FRONTEND_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontUrl'].OutputValue" \
  --output text)"

echo "CloudFrontUrl=$CLOUDFRONT_URL"
```

- デプロイ
  - CloudFrontToApiHeaderValueは任意の値を設定して下さい
  - HostKeyも任意の値を設定して下さい

```bash
cd backend
sam build
sam deploy \
  --stack-name "$BACKEND_STACK_NAME" \
  --region "$AWS_REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    StageName=dev  \
    FrontendOrigin="$CLOUDFRONT_URL" \
    CloudFrontToApiHeaderValue="$API_HEADER_VALUE" \
    HostKey=123456789 \
    CloudFrontDNSName="$CLOUDFRONT_URL" \
    GeoRestrictionLocations=JP
```

- API EndpointのURLを変数として格納する

```bash
API_ENDPOINT="$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$BACKEND_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" \
  --output text)"

echo "ApiEndpoint=$API_ENDPOINT"
```

### フロントエンドのデプロイ

- この時点ではまだコンテンツファイルはアップロードしません。

```bash
cd frontend
sam build
sam deploy \
  --stack-name "$FRONTEND_STACK_NAME" \
  --region "$AWS_REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    CloudFrontToApiHeaderValue="$API_HEADER_VALUE" \
    ApiGatewayDomainName=$API_ENDPOINT \
    ApiGatewayOriginPath=/dev
```

- フロントエンド用S3バケット名を変数として格納する

```bash
BUCKET_NAME="$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$FRONTEND_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" \
  --output text)"

echo "BucketName=$BUCKET_NAME"
```

- index.htmlとconfig.jsonのアップロード

```bash
cd ../frontend
aws s3 sync public "s3://$BUCKET_NAME/" --delete
```

### 動作確認

`echo "https://$CLOUDFRONT_URL"`

## 仕様

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

  FEH -->|2 POST /quiz/next| GW
  FET -->|9 POST /quiz/judge| GW
  FEH -.->|17 GET /quiz/current, /scores| GW
  FET -.->|17 GET /quiz/current, /scores| GW
```

### 2) 出題フロー（NextQuiz）

```mermaid
flowchart LR
  FEH[Host UI] -->|2 POST /quiz/next| GW[API Gateway]
  GW -->|3 Invoke| NQ[get_next_quiz]

  subgraph Core["AI / Data"]
    MCP[AWS Knowledge MCP]
    BR[Bedrock]
    DDB[DynamoDB]
  end

  NQ -->|4 Knowledge| MCP
  NQ -->|5 Generate| BR
  NQ -->|6 Save| DDB
  NQ -->|7 Quiz| GW
  GW -->|8 Response| FEH
```

### 3) 採点＋同期（Judge / Sync）

```mermaid
flowchart LR
  FET[Team UI] -->|9 POST /quiz/judge| GW[API Gateway]
  GW -->|10 Invoke| JA[judge_answer]

  subgraph Core["AI / Data"]
    MCP[AWS Knowledge MCP]
    BR[Bedrock]
    DDB[DynamoDB]
  end

  JA -->|11 Load| DDB
  JA -->|12 Knowledge| MCP
  JA -->|13 Judge| BR
  JA -->|14 Save| DDB
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

### シーケンス図

- 「次のクイズ」ボタンを押したとき

```mermaid
sequenceDiagram
  autonumber
  actor Host as Host(Browser)
  participant FEH as Host UI
  participant GW as API Gateway
  participant NQ as get_next_quiz
  participant MCP as Knowledge MCP
  participant BR as Bedrock
  participant DB as DynamoDB

  Host->>FEH: 「次のクイズ」クリック
  FEH->>GW: POST /quiz/next (構成図:2)
  GW->>NQ: Invoke (構成図:3)

  NQ->>MCP: ナレッジ検索 (構成図:4)
  MCP-->>NQ: ナレッジ要点 (構成図:4)

  NQ->>BR: ナレッジ + 条件でクイズ生成 (構成図:5)
  BR-->>NQ: クイズ（問題/選択肢/正解/根拠）(構成図:5)

  NQ->>DB: currentQuiz / 状態保存 (構成図:6)
  DB-->>NQ: OK (構成図:6)

  NQ-->>GW: quiz JSON (構成図:7)
  GW-->>FEH: quiz JSON (構成図:8)
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
  FET->>GW: POST /quiz/judge (構成図:9)
  GW->>JA: Invoke (構成図:10)

  JA->>DB: クイズ情報取得 (構成図:11)
  DB-->>JA: quiz context (構成図:11)

  JA->>MCP: 根拠ナレッジ取得 (構成図:12)
  MCP-->>JA: ナレッジ要点 (構成図:12)

  JA->>BR: 回答判定依頼（rubric）(構成図:13)
  BR-->>JA: score / feedback (構成図:13)

  JA->>DB: スコア・履歴保存 (構成図:14)
  DB-->>JA: OK (構成図:14)

  JA-->>GW: result JSON (構成図:15)
  GW-->>FET: result JSON (構成図:16)
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
    FE->>GW: GET /quiz/current (構成図:17)
    GW->>GC: Invoke (構成図:18)
    GC->>DB: Get currentQuiz
    DB-->>GC: quiz
    GC-->>GW: quiz
    GW-->>FE: quiz
  end

  loop 定期ポーリング（スコア）
    FE->>GW: GET /scores (構成図:17)
    GW->>GS: Invoke (構成図:19)
    GS->>DB: Get scores
    DB-->>GS: scores
    GS-->>GW: scores
    GW-->>FE: scores
  end
```

## 主なロジック

クイズや採点アルゴリズムは [logic.md](./docs/logic.md) を参照下さい。

## クイズの出題内容を調整する場合

[ファイル名：app.py](./backend/src/get_next_quiz/app.py)

Bedrockに渡すプロンプトを調整します。
下記がソースコード内で指定しています。

- SYSTEM_PROMPT
  - クイズのルールを定義しています
- USER_PROMPT_TEMPLATE
  - クイズの生成アルゴリズムを定義しています
- QUERYSETS
  - AWS Knowledge MCP Serverで検索するためのクイズカテゴリごとのキーワードを定義しています

## Bedrockのコンフィグ

[ファイル名：bedrock.py](./backend/layers/common/python/common/bedrock.py)

`inferenceConfig={"temperature": 0.2, "maxTokens": 2200}` を定義しています。
temperatureを **0.4** など大きくするとクイズや採点文言の表現が揺らぎます。
