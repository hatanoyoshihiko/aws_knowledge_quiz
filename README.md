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
  --s3-bucket aws-sam-cli-managed-default-xxx \
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
  --s3-bucket aws-sam-cli-managed-default-xxx \
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

### システム全体構成図

> ※ 本図は処理全体の俯瞰を目的としており、
> クイズ状態・スコアの同期（GET /quiz/current, /scores）は
> ポーリングとして簡略表現しています。
> 詳細な処理順は以下のシーケンス図を参照してください。

```mermaid
flowchart LR
    %% ===== Edge / Frontend =====
    subgraph Edge["Edge / Frontend"]
        User[User Browser]
        CF[CloudFront]
        FEH[Host UI<br/>host.html]
        FET[Team UI<br/>team.html]

        User -->|0 Access| CF
        CF -->|1 UI| FEH
        CF -->|1' UI| FET
    end

    %% ===== API / Control =====
    subgraph API["API / Control Layer"]
        GW[API Gateway<br/>HTTP API]

        NQ[get_next_quiz]
        JA[judge_answer]
        GC[get_current_quiz]
        GS[get_scores]
    end

    %% ===== AI / Data =====
    subgraph Core["AI / Data Layer"]
        MCP[AWS Knowledge<br/>MCP Server]
        BR[Amazon Bedrock<br/>LLM]
        DDB[DynamoDB<br/>Quiz / State / Scores]
    end

    %% ===== Quiz generation =====
    FEH -->|2 POST /quiz/next| GW
    GW -->|3| NQ
    NQ -->|4 Knowledge| MCP
    NQ -->|5 Generate| BR
    NQ -->|6 Save| DDB
    NQ -->|7 Quiz| GW
    GW -->|8 Response| FEH

    %% ===== Judge =====
    FET -->|9 POST /quiz/judge| GW
    GW -->|10| JA
    JA -->|11 Load| DDB
    JA -->|12 Knowledge| MCP
    JA -->|13 Judge| BR
    JA -->|14 Save| DDB
    JA -->|15 Result| GW
    GW -->|16 Response| FET

    %% ===== Sync =====
    FEH -.->|17 GET current/scores| GW
    FET -.->|17 GET current/scores| GW
    GW -->|18| GC
    GW -->|18| GS
    GC --> DDB
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
  FEH->>GW: POST /quiz/next
  GW->>NQ: Invoke

  NQ->>MCP: ナレッジ検索
  MCP-->>NQ: ナレッジ要点

  NQ->>BR: ナレッジ + 条件でクイズ生成
  BR-->>NQ: クイズ（問題/選択肢/正解/根拠）

  NQ->>DB: currentQuiz / 状態保存
  DB-->>NQ: OK

  NQ-->>GW: quiz JSON
  GW-->>FEH: quiz JSON
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
  FET->>GW: POST /quiz/judge
  GW->>JA: Invoke

  JA->>DB: クイズ情報取得
  DB-->>JA: quiz context

  JA->>MCP: 根拠ナレッジ取得
  MCP-->>JA: ナレッジ要点

  JA->>BR: 回答判定依頼（rubric）
  BR-->>JA: score / feedback

  JA->>DB: スコア・履歴保存
  DB-->>JA: OK

  JA-->>GW: result JSON
  GW-->>FET: result JSON
```

- クイズの表示同期（全クライアント）

```mermaid
sequenceDiagram
  autonumber
  participant FE as Frontend
  participant GW as API Gateway
  participant DB as DynamoDB

  loop 定期ポーリング
    FE->>GW: GET /quiz/current
    GW->>DB: currentQuiz 取得
    DB-->>GW: quiz
    GW-->>FE: quiz
  end

  loop 定期ポーリング
    FE->>GW: GET /scores
    GW->>DB: scores 取得
    DB-->>GW: scores
    GW-->>FE: scores
  end
```

## 主なロジック

[logic.md](./docs/logic.md) を

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
