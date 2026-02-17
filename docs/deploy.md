# デプロイ方法

## 前準備

- リポジトリのクローン

```bash

gitclonehttps://github.com/hatanoyoshihiko/aws_knowledge_quiz.git

```

- 変数定義

```bash

exportAWS_REGION=ap-northeast-1

exportFRONTEND_STACK_NAME=aws-knowledge-quiz-frontend

exportBACKEND_STACK_NAME=aws-knowledge-quiz-backend

exportAWS_PROFILE=YOUR_PROFILE

exportAPI_HEADER_VALUE=YOUR_SECRET_HEADER_VALUE

```

## バックエンドのデプロイ

- CloudFrontのURLを変数として格納する

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

cdbackend

sambuild

samdeploy \

  --stack-name "$BACKEND_STACK_NAME" \

  --region"$AWS_REGION" \

  --resolve-s3 \

  --capabilitiesCAPABILITY_IAM \

  --parameter-overrides \

    StageName=dev \

    FrontendOrigin="https://d1234567890abc.cloudfront.net" \

    CloudFrontToApiHeaderValue="$API_HEADER_VALUE" \

    HostKey=YOUR_HOST_KEY \

    CloudFrontDNSName="d1234567890abc.cloudfront.net" \

    GeoRestrictionLocations=JP \

    BedrockGuardrailIdentifier=YOUR_GUARDRAIL_ID \

    BedrockGuardrailVersion=1

```

- API EndpointのURLを変数として格納する

```bash

API_ENDPOINT="$(aws cloudformation describe-stacks \

  --region "$AWS_REGION" \

  --stack-name "$BACKEND_STACK_NAME" \

  --query "Stacks[0].Outputs[?OutputKey=='ApiGatewayDomainName'].OutputValue" \

  --output text)"


API_ORIGIN_PATH="$(aws cloudformation describe-stacks \

  --region "$AWS_REGION" \

  --stack-name "$BACKEND_STACK_NAME" \

  --query "Stacks[0].Outputs[?OutputKey=='ApiGatewayOriginPath'].OutputValue" \

  --output text)"


echo"ApiGatewayDomainName=$API_ENDPOINT"

echo"ApiGatewayOriginPath=$API_ORIGIN_PATH"

```

## フロントエンドのデプロイ

- この時点ではまだコンテンツファイルはアップロードしません。

  - CloudFrontToApiHeaderValue: バックエンドと同じ値を設定
  - ApiGatewayDomainName: API Gatewayのドメイン名（例: abc123.execute-api.ap-northeast-1.amazonaws.com）
  - ApiGatewayOriginPath: API Gatewayのステージパス（例: /dev）

```bash

cdfrontend

sambuild

samdeploy \

  --stack-name "$FRONTEND_STACK_NAME" \

  --region"$AWS_REGION" \

  --resolve-s3 \

  --capabilitiesCAPABILITY_IAM \

  --parameter-overrides \

    CloudFrontToApiHeaderValue="$API_HEADER_VALUE" \

    ApiGatewayDomainName="abc123.execute-api.ap-northeast-1.amazonaws.com" \

    ApiGatewayOriginPath=/dev

```

- フロントエンド用S3バケット名を変数として格納する

```bash

BUCKET_NAME="$(aws cloudformation describe-stacks \

  --region "$AWS_REGION" \

  --stack-name "$FRONTEND_STACK_NAME" \

  --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" \

  --output text)"


echo"BucketName=$BUCKET_NAME"

```

- index.htmlとconfig.jsonのアップロード

```bash

cd../frontend

awss3syncpublic"s3://$BUCKET_NAME/"--delete

```

## 動作確認

`echo "https://$CLOUDFRONT_URL"`
