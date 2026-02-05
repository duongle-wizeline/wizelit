#!/bin/bash
# =============================================================================
# Check Wizelit deployment status
# =============================================================================

set -e

AWS_REGION="${AWS_REGION:-ap-southeast-2}"
CLUSTER_NAME="wizelit-cluster"
STACK_NAME="wizelit-dev"

echo "📊 Wizelit Deployment Status"
echo "============================"
echo ""

# Check if stack exists
STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --query 'Stacks[0].StackStatus' \
  --output text 2>/dev/null || echo "NOT_FOUND")

echo "📦 Stack: $STACK_NAME"
echo "   Status: $STACK_STATUS"
echo ""

if [ "$STACK_STATUS" == "NOT_FOUND" ]; then
  echo "💡 Stack not deployed. Run: cd cdk && cdk deploy"
  exit 0
fi

# ECS Service status
echo "🐳 ECS Service:"
SERVICE_INFO=$(aws ecs list-services \
  --cluster "$CLUSTER_NAME" \
  --region "$AWS_REGION" \
  --query 'serviceArns[0]' \
  --output text 2>/dev/null || echo "None")

if [ "$SERVICE_INFO" != "None" ] && [ -n "$SERVICE_INFO" ]; then
  SERVICE_NAME=$(basename "$SERVICE_INFO")
  
  RUNNING_COUNT=$(aws ecs describe-services \
    --cluster "$CLUSTER_NAME" \
    --services "$SERVICE_NAME" \
    --region "$AWS_REGION" \
    --query 'services[0].runningCount' \
    --output text)
  
  DESIRED_COUNT=$(aws ecs describe-services \
    --cluster "$CLUSTER_NAME" \
    --services "$SERVICE_NAME" \
    --region "$AWS_REGION" \
    --query 'services[0].desiredCount' \
    --output text)
  
  echo "   Service: $SERVICE_NAME"
  echo "   Running: $RUNNING_COUNT / $DESIRED_COUNT tasks"
  
  if [ "$RUNNING_COUNT" == "0" ]; then
    echo "   💤 Service is scaled down (saving costs)"
  else
    echo "   ✅ Service is running"
  fi
else
  echo "   ❌ No service found"
fi

echo ""

# ALB URL
ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`AlbDnsName`].OutputValue' \
  --output text 2>/dev/null || echo "")

if [ -n "$ALB_DNS" ] && [ "$ALB_DNS" != "None" ]; then
  echo "🌐 URL: http://$ALB_DNS"
fi

echo ""
echo "💡 Commands:"
echo "   Scale down: ./scripts/aws-scale-down.sh"
echo "   Scale up:   ./scripts/aws-scale-up.sh"
echo "   Destroy:    cd cdk && cdk destroy"
