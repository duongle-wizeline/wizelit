#!/bin/bash
# =============================================================================
# Scale down Wizelit ECS service to 0 tasks (save Fargate costs)
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

echo "📉 Scaling down service: $SERVICE_NAME to 0 tasks..."

aws ecs update-service \
  --cluster "$CLUSTER_NAME" \
  --service "$SERVICE_NAME" \
  --desired-count 0 \
  --region "$AWS_REGION" \
  --no-cli-pager

echo "✅ Service scaled to 0. Fargate costs stopped!"
echo ""
echo "💡 To scale back up, run: ./scripts/aws-scale-up.sh"
