# デプロイ方法

## 前提条件

このアプリケーションをデプロイするには、以下の環境が必要です：

- AWSアカウント
- AWS CLI
- AWS SAM CLI
- Python 3.14以上（Lambdaランタイム互換）
- Git

## 環境セットアップ

### 1. AWS CLIのインストール

#### macOS (Homebrew)

```bash
brew install awscli
aws --version
```

#### Windows

[AWS CLI公式インストーラー](https://aws.amazon.com/cli/)からダウンロードしてインストール

#### Linux

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
aws --version
```

### 2. AWS SAM CLIのインストール

#### macOS (Homebrew)

```bash
brew install aws-sam-cli
sam --version
```

#### Windows

[AWS SAM CLI公式インストーラー](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)からダウンロードしてインストール

#### Linux

```bash
# pipを使用
pip install aws-sam-cli
sam --version
```

### 3. AWSクレデンシャルの設定

#### IAMユーザーの作成

1. [AWS IAMコンソール](https://console.aws.amazon.com/iam/)にアクセス
2. 「ユーザー」→「ユーザーを追加」
3. ユーザー名を入力（例: `sam-deploy-user`）
4. 「アクセスキー - プログラムによるアクセス」を選択
5. 以下のポリシーをアタッチ：
   - `AdministratorAccess`（推奨：デプロイ時のみ）
   - または、最小権限ポリシー（下記参照）
6. アクセスキーIDとシークレットアクセスキーを保存

#### 最小権限ポリシー（推奨）

デプロイに必要な最小限の権限：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "cloudformation:*",
        "s3:*",
        "lambda:*",
        "apigateway:*",
        "iam:*",
        "dynamodb:*",
        "logs:*",
        "cloudfront:*",
        "bedrock:*"
      ],
      "Resource": "*"
    }
  ]
}
```

#### AWS CLIの設定

```bash
aws configure
```

以下の情報を入力：

- AWS Access Key ID: （IAMユーザーのアクセスキーID）
- AWS Secret Access Key: （IAMユーザーのシークレットアクセスキー）
- Default region name: `ap-northeast-1`（東京リージョン）
- Default output format: `json`

#### プロファイルを使用する場合

複数のAWSアカウントを使い分ける場合：

```bash
aws configure --profile YOUR_PROFILE_NAME
```

設定確認：

```bash
# デフォルトプロファイル
aws sts get-caller-identity

# 特定のプロファイル
aws sts get-caller-identity --profile my-project
```

### 4. Bedrockモデルアクセスの有効化

1. [Amazon Bedrockコンソール](https://console.aws.amazon.com/bedrock/)にアクセス
2. 左メニューから「Model access」を選択
3. 「Manage model access」をクリック
4. 以下のモデルを有効化：
   - Claude Sonnet 4.6（推奨）
5. 「Save changes」をクリック
6. ステータスが「Access granted」になるまで待機（数分）

### 5. リポジトリのクローン

```bash
git clone https://github.com/hatanoyoshihiko/aws_knowledge_quiz.git
cd aws_knowledge_quiz
```

### 6. 環境変数の設定

デプロイに必要な変数を定義：

```bash
export AWS_REGION=ap-northeast-1
export FRONTEND_STACK_NAME=aws-knowledge-quiz-frontend
export BACKEND_STACK_NAME=aws-knowledge-quiz-backend
export AWS_PROFILE=default  # または YOUR_PROFILE_NAME
export API_HEADER_VALUE=$(openssl rand -hex 32)  # ランダムな秘密文字列を生成
export HOST_KEY=$(openssl rand -hex 16)  # 出題者用キー（16進数32文字）
export BEDROCK_MODEL_ID=jp.anthropic.claude-sonnet-4-6  # Optional
```

設定確認：

```bash
echo "AWS_REGION: $AWS_REGION"
echo "FRONTEND_STACK_NAME: $FRONTEND_STACK_NAME"
echo "BACKEND_STACK_NAME: $BACKEND_STACK_NAME"
echo "API_HEADER_VALUE: $API_HEADER_VALUE"
echo "HOST_KEY: $HOST_KEY"
```

**重要**: `API_HEADER_VALUE`と `HOST_KEY`は安全に保管してください。これらは認証に使用されます。

## デプロイ順序

このアプリケーションは以下の順序でデプロイ：

1. **フロントエンド初回デプロイ** → CloudFront URLを取得
2. **バックエンドデプロイ** → FrontendOriginにCloudFront URLを指定、API Gateway URLを取得
3. **フロントエンド再デプロイ** → API設定を反映
4. **静的ファイルアップロード** → S3にHTMLやJSをアップロード

## 1. フロントエンド初回デプロイ

フロントエンドをデプロイしてCloudFront URLを取得。
この時点ではバックエンドがまだ存在しないため、ダミー値でAPI設定を行います。

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
    ApiGatewayDomainName="dummy.execute-api.ap-northeast-1.amazonaws.com" \
    ApiGatewayOriginPath="/dummy"
```

CloudFront URLを変数として格納：

```bash
CLOUDFRONT_URL=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$FRONTEND_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontUrl'].OutputValue" \
  --output text)

echo "CloudFrontUrl=$CLOUDFRONT_URL"
```

## 2. バックエンドのデプロイ

バックエンドをデプロイして、API Gateway情報を取得します。

```bash
cd ../backend
sam build
sam deploy \
  --stack-name "$BACKEND_STACK_NAME" \
  --region "$AWS_REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    StageName=prod \
    FrontendOrigin="$CLOUDFRONT_URL" \
    CloudFrontToApiHeaderValue="$API_HEADER_VALUE" \
    HostKey="$HOST_KEY" \
    BedrockModelId="${BEDROCK_MODEL_ID:-jp.anthropic.claude-sonnet-4-6}"
```

デプロイオプション：

- StageName: API Gatewayのステージ名（例: dev, prod）
- FrontendOrigin: CloudFrontのURL（CORS設定用）
- CloudFrontToApiHeaderValue: CloudFrontからAPI Gatewayへの認証用ヘッダー値（任意の秘密文字列）
- HostKey: 出題者側UIで入力するキー（任意の値、例: 123456789）
- BedrockModelId: モデルID（デフォルト: jp.anthropic.claude-sonnet-4-6）
- BedrockGuardrailIdentifier: Bedrock Guardrail ID（オプション）
- BedrockGuardrailVersion: Bedrock Guardrail Version（オプション、デフォルト: DRAFT）

API Gateway情報を変数として格納：

```bash
API_ENDPOINT=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$BACKEND_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiGatewayDomainName'].OutputValue" \
  --output text)

API_ORIGIN_PATH=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$BACKEND_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiGatewayOriginPath'].OutputValue" \
  --output text)

echo "ApiGatewayDomainName=$API_ENDPOINT"
echo "ApiGatewayOriginPath=$API_ORIGIN_PATH"
```

## 3. フロントエンド再デプロイ (API設定反映)

バックエンドから取得したAPI情報で、フロントエンドを再デプロイします。

```bash
cd ../frontend
sam deploy \
  --stack-name "$FRONTEND_STACK_NAME" \
  --region "$AWS_REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    CloudFrontToApiHeaderValue="$API_HEADER_VALUE" \
    ApiGatewayDomainName="$API_ENDPOINT" \
    ApiGatewayOriginPath="$API_ORIGIN_PATH"
```

## 4. 静的ファイルアップロード

- フロントエンド用S3バケット名を取得してファイルをアップロード

```bash
BUCKET_NAME=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$FRONTEND_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" \
  --output text)

echo "BucketName=$BUCKET_NAME"
```

- HTMLとJSファイルをアップロード

```bash
aws s3 sync public "s3://$BUCKET_NAME/" --delete
```

## 動作確認

デプロイ完了後、CloudFront URLにアクセスしてアプリケーションを確認：

```bash
echo "Application URL: $CLOUDFRONT_URL"
```

## トラブルシューティング

### API接続エラーが発生する場合

フロントエンドの再デプロイ（手順3）が正しく完了しているか確認：

```bash
aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$FRONTEND_STACK_NAME" \
  --query "Stacks[0].Parameters[?ParameterKey=='ApiGatewayDomainName'].ParameterValue" \
  --output text
```

ダミー値（`dummy.execute-api...`）が表示される場合は、手順3を再実行してください。

### CloudFrontキャッシュのクリア

設定変更後にキャッシュが残っている場合：

```bash
DISTRIBUTION_ID=$(aws cloudformation describe-stack-resources \
  --region "$AWS_REGION" \
  --stack-name "$FRONTEND_STACK_NAME" \
  --query "StackResources[?ResourceType=='AWS::CloudFront::Distribution'].PhysicalResourceId" \
  --output text)

aws cloudfront create-invalidation \
  --distribution-id "$DISTRIBUTION_ID" \
  --paths "/*"
```

## デプロイパラメータ一覧

### バックエンド (backend/template.yaml)

| パラメータ                 | 説明                                          | デフォルト値                   |
| -------------------------- | --------------------------------------------- | ------------------------------ |
| StageName                  | API Gatewayのステージ名                       | dev                            |
| FrontendOrigin             | CloudFrontのURL（CORS設定用）                 | 必須                           |
| CloudFrontToApiHeaderValue | CloudFrontからAPI Gatewayへの認証用ヘッダー値 | 必須                           |
| HostKey                    | 出題者側UIで入力するキー                      | 必須                           |
| BedrockModelId             | Bedrockモデル                                 | jp.anthropic.claude-sonnet-4-6 |
| BedrockGuardrailIdentifier | Bedrock Guardrail ID                          | （オプション）                 |
| BedrockGuardrailVersion    | Bedrock Guardrail Version                     | DRAFT                          |

### フロントエンド (frontend/template.yaml)

| パラメータ                 | 説明                       | デフォルト値 |
| -------------------------- | -------------------------- | ------------ |
| CloudFrontToApiHeaderValue | バックエンドと同じ値を設定 | 必須         |
| ApiGatewayDomainName       | API Gatewayのドメイン名    | 必須         |
| ApiGatewayOriginPath       | API Gatewayのステージパス  | 必須         |
