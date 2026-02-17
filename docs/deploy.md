# デプロイ方法

## 前準備

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
export API_HEADER_VALUE=YOUR_SECRET_HEADER_VALUE
```

## デプロイ順序

このアプリケーションは以下の順序でデプロイします：

1. **フロントエンド初回デプロイ** → CloudFront URLを取得
2. **バックエンドデプロイ** → FrontendOriginにCloudFront URLを指定、API Gateway URLを取得
3. **フロントエンド再デプロイ** → API設定を反映
4. **静的ファイルアップロード** → S3にHTMLやJSをアップロード

## 1. フロントエンド初回デプロイ

まずフロントエンドをデプロイしてCloudFront URLを取得します。

```bash
cd frontend
sam build
sam deploy \
  --stack-name "$FRONTEND_STACK_NAME" \
  --region "$AWS_REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM
```

CloudFront URLを変数として格納します：

```bash
CLOUDFRONT_URL=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$FRONTEND_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontUrl'].OutputValue" \
  --output text)

echo "CloudFrontUrl=$CLOUDFRONT_URL"
```

## 2. バックエンドのデプロイ

```bash

CLOUDFRONT_URL="$(aws cloudformation describe-stacks \

  --region "$AWS_REGION" \

  --stack-name "$FRONTEND_STACK_NAME" \

  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontUrl'].OutputValue" \

  --output text)"


echo"CloudFrontUrl=$CLOUDFRONT_URL"

```

- デプロイオプション

  - StageName: API Gatewayのステージ名（例: dev, prod）
  - FrontendOrigin: CloudFrontのURL（CORS設定用）
  - CloudFrontToApiHeaderValue: CloudFrontからAPI Gatewayへの認証用ヘッダー値（任意の秘密文字列）
  - HostKey: 出題者側UIで入力するキー（任意の値、例: 123456789）
  - CloudFrontDNSName: CloudFrontのドメイン名
  - GeoRestrictionLocations: 地理的制限（例: JP、複数の場合はカンマ区切り）
  - BedrockGuardrailIdentifier: Bedrock Guardrail ID（オプション）
  - BedrockGuardrailVersion: Bedrock Guardrail Version（オプション、デフォルト: DRAFT）

```bash
cd backend
sam build
sam deploy \
  --stack-name "$BACKEND_STACK_NAME" \
  --region "$AWS_REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    StageName=dev \
    FrontendOrigin="$CLOUDFRONT_URL" \
    CloudFrontToApiHeaderValue="$API_HEADER_VALUE" \
    HostKey=YOUR_HOST_KEY \
    CloudFrontDNSName="d1234567890abc.cloudfront.net" \
    GeoRestrictionLocations=JP \
    BedrockGuardrailIdentifier=YOUR_GUARDRAIL_ID \
    BedrockGuardrailVersion=1
```

API Gateway情報を変数として格納します：

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

```bash
cd frontend
sam deploy \
  --parameter-overrides \
    CloudFrontToApiHeaderValue="$API_HEADER_VALUE" \
    ApiGatewayDomainName="$API_ENDPOINT" \
    ApiGatewayOriginPath="$API_ORIGIN_PATH"
```

## 4. 静的ファイルアップロード

フロントエンド用S3バケット名を取得してファイルをアップロードします：

```bash
BUCKET_NAME=$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$FRONTEND_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" \
  --output text)

echo "BucketName=$BUCKET_NAME"

# HTMLとJSファイルをアップロード
aws s3 sync public "s3://$BUCKET_NAME/" --delete
```

## 動作確認

デプロイが完了したら、CloudFront URLにアクセスしてアプリケーションを確認します：

```bash
echo "https://$CLOUDFRONT_URL"
```

## デプロイパラメータ一覧

### バックエンド (backend/template.yaml)

| パラメータ | 説明 | デフォルト値 |
| --- | --- | --- |
| StageName | API Gatewayのステージ名 | dev |
| FrontendOrigin | CloudFrontのURL（CORS設定用） | 必須 |
| CloudFrontToApiHeaderValue | CloudFrontからAPI Gatewayへの認証用ヘッダー値 | 必須 |
| HostKey | 出題者側UIで入力するキー | 必須 |
| BedrockGuardrailIdentifier | Bedrock Guardrail ID | （オプション） |
| BedrockGuardrailVersion | Bedrock Guardrail Version | DRAFT |

### フロントエンド (frontend/template.yaml)

| パラメータ | 説明 | デフォルト値 |
| --- | --- | --- |
| CloudFrontToApiHeaderValue | バックエンドと同じ値を設定 | 必須 |
| ApiGatewayDomainName | API Gatewayのドメイン名 | 必須 |
| ApiGatewayOriginPath | API Gatewayのステージパス | 必須 |
