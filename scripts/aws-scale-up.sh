#!/bin/bash
# =============================================================================
# Scale up Wizelit ECS service to 1 task
# =============================================================================

set -e

AWS_REGION="${AWS_REGION:-ap-southeast-2}"
CLUSTER_NAME="wizelit-cluster"

echo "🔍 Finding ECS service in cluster: $CLUSTER_NAME..."

SERVICE_NAME=$(aws ecs list-services \
  --cluster "$CLUSTER_NAME" \
  --region "$AWS_REGION" \
  --query 'serviceArns[0]' \
  --output text | xargs basename)

if [ -z "$SERVICE_NAME" ] || [ "$SERVICE_NAME" == "None" ]; then
  echo "❌ No service found in cluster $CLUSTER_NAME"
  exit 1
fi

echo "📈 Scaling up service: $SERVICE_NAME to 1 task..."

aws ecs update-service \
  --cluster "$CLUSTER_NAME" \
  --service "$SERVICE_NAME" \
  --desired-count 1 \
  --region "$AWS_REGION" \
  --no-cli-pager

echo "⏳ Waiting for service to stabilize..."

aws ecs wait services-stable \
  --cluster "$CLUSTER_NAME" \
  --services "$SERVICE_NAME" \
  --region "$AWS_REGION"

# Get ALB URL
ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name wizelit-dev \
  --region "$AWS_REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`AlbDnsName`].OutputValue' \
  --output text 2>/dev/null || echo "")

echo "✅ Service is running!"
echo ""
if [ -n "$ALB_DNS" ] && [ "$ALB_DNS" != "None" ]; then
  echo "🌐 Access your app at: http://$ALB_DNS"
fi
