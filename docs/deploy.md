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
export API_HEADER_VALUE=YOUR_SECRET_HEADER_VALUE　# For Lambda Authorizer
export HOST_KEY=YOUR_QUIZ_HOST_KEY
```

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
    HostKey="$HOST_KEY"
```

デプロイオプション：
- StageName: API Gatewayのステージ名（例: dev, prod）
- FrontendOrigin: CloudFrontのURL（CORS設定用）
- CloudFrontToApiHeaderValue: CloudFrontからAPI Gatewayへの認証用ヘッダー値（任意の秘密文字列）
- HostKey: 出題者側UIで入力するキー（任意の値、例: 123456789）
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
