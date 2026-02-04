#!/bin/bash
# =============================================================================
# Wizelit Deployment Script
# Builds Docker image for AMD64 and deploys to AWS ECS
#
# Usage:
#   ./deploy.sh          - Full deploy (build, push, deploy)
#   ./deploy.sh status   - Check ECS service status
#   ./deploy.sh logs     - View recent CloudWatch logs
#   ./deploy.sh open     - Open app in browser
#   ./deploy.sh stop     - Scale down to 0 (save costs)
#   ./deploy.sh start    - Scale up to 1
# =============================================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
AWS_REGION="${AWS_REGION:-ap-southeast-2}"
ECR_REPOSITORY="wizelit"
ECS_CLUSTER="wizelit-cluster"

# Get service ARN helper
get_service_arn() {
    aws ecs list-services --cluster $ECS_CLUSTER --query 'serviceArns[0]' --output text --region $AWS_REGION 2>/dev/null
}

# Get load balancer URL helper
get_alb_url() {
    aws elbv2 describe-load-balancers \
        --query "LoadBalancers[?contains(LoadBalancerName, 'wizel')].DNSName" \
        --output text \
        --region $AWS_REGION 2>/dev/null
}

# Handle subcommands
case "${1:-deploy}" in
    status)
        echo -e "${BLUE}Checking ECS service status...${NC}"
        SERVICE_ARN=$(get_service_arn)
        aws ecs describe-services --cluster $ECS_CLUSTER --services $SERVICE_ARN --region $AWS_REGION \
            --query 'services[0].{Service:serviceName,Status:status,Running:runningCount,Desired:desiredCount,Pending:pendingCount}' \
            --no-cli-pager
        exit 0
        ;;
    logs)
        echo -e "${BLUE}Fetching recent logs...${NC}"
        aws logs tail /ecs/wizelit --since 10m --region $AWS_REGION --format short
        exit 0
        ;;
    open)
        ALB_URL=$(get_alb_url)
        if [ -n "$ALB_URL" ]; then
            echo -e "${GREEN}Opening http://$ALB_URL${NC}"
            open "http://$ALB_URL" 2>/dev/null || xdg-open "http://$ALB_URL" 2>/dev/null || echo "Visit: http://$ALB_URL"
        else
            echo -e "${RED}Could not find load balancer URL${NC}"
        fi
        exit 0
        ;;
    stop)
        echo -e "${YELLOW}Scaling down ECS service to 0...${NC}"
        SERVICE_ARN=$(get_service_arn)
        aws ecs update-service --cluster $ECS_CLUSTER --service $SERVICE_ARN --desired-count 0 --region $AWS_REGION --no-cli-pager > /dev/null
        echo -e "${GREEN}✓ Service scaled down. No running costs for ECS tasks.${NC}"
        exit 0
        ;;
    start)
        echo -e "${YELLOW}Scaling up ECS service to 1...${NC}"
        SERVICE_ARN=$(get_service_arn)
        aws ecs update-service --cluster $ECS_CLUSTER --service $SERVICE_ARN --desired-count 1 --region $AWS_REGION --no-cli-pager > /dev/null
        echo -e "${GREEN}✓ Service scaling up. Wait ~1-2 minutes for it to be ready.${NC}"
        exit 0
        ;;
    deploy|"")
        # Continue with deploy below (uses Docker cache)
        DOCKER_NO_CACHE=""
        ;;
    deploy-nocache)
        # Deploy without Docker cache (rebuilds everything)
        DOCKER_NO_CACHE="--no-cache"
        ;;
    *)
        echo "Usage: ./deploy_platform.sh [command]"
        echo ""
        echo "Commands:"
        echo "  deploy        - Full deploy (default, uses Docker cache)"
        echo "  deploy-nocache - Full deploy without Docker cache"
        echo "  status        - Check ECS service status"
        echo "  logs          - View recent CloudWatch logs"
        echo "  open          - Open app in browser"
        echo "  stop          - Scale down to 0 (save costs)"
        echo "  start         - Scale up to 1"
        exit 1
        ;;
esac

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Wizelit Deployment Script${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if AWS CLI is configured
echo -e "${YELLOW}[1/6] Checking AWS credentials...${NC}"
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    echo -e "${RED}Error: AWS credentials not configured. Run 'aws configure' first.${NC}"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY"

echo -e "${GREEN}✓ AWS Account: $ACCOUNT_ID${NC}"
echo -e "${GREEN}✓ Region: $AWS_REGION${NC}"
echo ""

# Login to ECR
echo -e "${YELLOW}[2/6] Logging in to Amazon ECR...${NC}"
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
echo -e "${GREEN}✓ ECR login successful${NC}"
echo ""

# Build Docker image for AMD64 (required for AWS Fargate)
echo -e "${YELLOW}[3/6] Building Docker image (linux/amd64)...${NC}"
if [ -n "$DOCKER_NO_CACHE" ]; then
    echo -e "${BLUE}   Building WITHOUT cache (clean rebuild)...${NC}"
else
echo -e "${BLUE}   This may take a few minutes...${NC}"
fi
docker build --platform linux/amd64 $DOCKER_NO_CACHE -t $ECR_REPOSITORY .
echo -e "${GREEN}✓ Docker build complete${NC}"
echo ""

# Tag the image
echo -e "${YELLOW}[4/6] Tagging image...${NC}"
docker tag $ECR_REPOSITORY:latest $ECR_URI:latest
docker tag $ECR_REPOSITORY:latest $ECR_URI:$(git rev-parse --short HEAD 2>/dev/null || echo "manual")
echo -e "${GREEN}✓ Image tagged${NC}"
echo ""

# Push to ECR
echo -e "${YELLOW}[5/6] Pushing image to ECR...${NC}"
docker push $ECR_URI:latest
echo -e "${GREEN}✓ Image pushed to ECR${NC}"
echo ""

# Get ECS service name
echo -e "${YELLOW}[6/6] Deploying to ECS...${NC}"
SERVICE_ARN=$(aws ecs list-services --cluster $ECS_CLUSTER --query 'serviceArns[0]' --output text --region $AWS_REGION)

if [ "$SERVICE_ARN" == "None" ] || [ -z "$SERVICE_ARN" ]; then
    echo -e "${RED}Error: No ECS service found in cluster $ECS_CLUSTER${NC}"
    exit 1
fi

# Force new deployment
aws ecs update-service \
    --cluster $ECS_CLUSTER \
    --service $SERVICE_ARN \
    --force-new-deployment \
    --region $AWS_REGION \
    --no-cli-pager > /dev/null

echo -e "${GREEN}✓ Deployment triggered${NC}"
echo ""

echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}  Deployment initiated successfully!${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "The deployment is now in progress. You can:"
echo -e "  • Check status: ${YELLOW}./deploy.sh status${NC}"
echo -e "  • View logs:    ${YELLOW}./deploy.sh logs${NC}"
echo -e "  • Open app:     ${YELLOW}./deploy.sh open${NC}"
echo ""

# Get load balancer URL
ALB_URL=$(aws elbv2 describe-load-balancers \
    --query "LoadBalancers[?contains(LoadBalancerName, 'wizel')].DNSName" \
    --output text \
    --region $AWS_REGION 2>/dev/null || echo "")

if [ -n "$ALB_URL" ]; then
    echo -e "App URL: ${GREEN}http://$ALB_URL${NC}"
fi
