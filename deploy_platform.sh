#!/bin/bash
# =============================================================================
# Wizelit Deployment Script
# Builds Docker image for AMD64 and deploys to AWS ECS
#
# Usage:
#   ./deploy_platform.sh              - Full deploy (build, push, deploy)
#   ./deploy_platform.sh deploy-nocache - Full deploy without Docker cache
#   ./deploy_platform.sh status       - Check ECS service status
#   ./deploy_platform.sh logs         - View recent CloudWatch logs
#   ./deploy_platform.sh open         - Open app in browser
#   ./deploy_platform.sh stop         - Scale ECS to 0 (quick, saves ~$10/mo)
#   ./deploy_platform.sh start        - Scale ECS to 1
#   ./deploy_platform.sh hibernate    - Stop ECS + RDS (saves ~$25/mo)
#   ./deploy_platform.sh wake         - Start RDS + ECS
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
RDS_IDENTIFIER="wizelit-db"  # RDS instance identifier

# Get service ARN helper
get_service_arn() {
    aws ecs list-services --cluster $ECS_CLUSTER --query 'serviceArns[0]' --output text --region $AWS_REGION 2>/dev/null
}

# Get RDS instance identifier (auto-detect from stack)
get_rds_identifier() {
    aws rds describe-db-instances --region $AWS_REGION \
        --query "DBInstances[?contains(DBInstanceIdentifier, 'wizelit')].DBInstanceIdentifier" \
        --output text 2>/dev/null | head -1
}

# Get RDS status
get_rds_status() {
    local rds_id=$(get_rds_identifier)
    if [ -n "$rds_id" ]; then
        aws rds describe-db-instances --db-instance-identifier "$rds_id" --region $AWS_REGION \
            --query 'DBInstances[0].DBInstanceStatus' --output text 2>/dev/null
    fi
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
    hibernate)
        echo -e "${YELLOW}🛌 Hibernating Wizelit (stopping ECS + RDS)...${NC}"
        echo ""
        
        # Stop ECS
        echo -e "${BLUE}[1/2] Scaling down ECS to 0...${NC}"
        SERVICE_ARN=$(get_service_arn)
        if [ -n "$SERVICE_ARN" ] && [ "$SERVICE_ARN" != "None" ]; then
            aws ecs update-service --cluster $ECS_CLUSTER --service $SERVICE_ARN --desired-count 0 --region $AWS_REGION --no-cli-pager > /dev/null
            echo -e "${GREEN}✓ ECS stopped${NC}"
        else
            echo -e "${YELLOW}⚠ No ECS service found${NC}"
        fi
        
        # Stop RDS
        echo -e "${BLUE}[2/2] Stopping RDS database...${NC}"
        RDS_ID=$(get_rds_identifier)
        if [ -n "$RDS_ID" ]; then
            RDS_STATUS=$(get_rds_status)
            if [ "$RDS_STATUS" == "available" ]; then
                aws rds stop-db-instance --db-instance-identifier "$RDS_ID" --region $AWS_REGION --no-cli-pager > /dev/null 2>&1 || true
                echo -e "${GREEN}✓ RDS stopping (takes ~5 minutes)${NC}"
            elif [ "$RDS_STATUS" == "stopped" ]; then
                echo -e "${YELLOW}⚠ RDS already stopped${NC}"
            else
                echo -e "${YELLOW}⚠ RDS status: $RDS_STATUS (cannot stop)${NC}"
            fi
        else
            echo -e "${YELLOW}⚠ No RDS instance found${NC}"
        fi
        
        echo ""
        echo -e "${GREEN}🛌 Hibernate complete!${NC}"
        echo -e "${BLUE}   Estimated savings: ~\$25/month (ECS + RDS)${NC}"
        echo -e "${YELLOW}   ⚠ Note: RDS auto-restarts after 7 days${NC}"
        echo -e "${YELLOW}   ⚠ ALB + ElastiCache cannot be stopped (~\$30/month still running)${NC}"
        echo ""
        echo -e "To wake up: ${YELLOW}./deploy_platform.sh wake${NC}"
        exit 0
        ;;
    wake)
        echo -e "${YELLOW}☀️ Waking up Wizelit (starting RDS + ECS)...${NC}"
        echo ""
        
        # Start RDS first (takes longer)
        echo -e "${BLUE}[1/2] Starting RDS database...${NC}"
        RDS_ID=$(get_rds_identifier)
        if [ -n "$RDS_ID" ]; then
            RDS_STATUS=$(get_rds_status)
            if [ "$RDS_STATUS" == "stopped" ]; then
                aws rds start-db-instance --db-instance-identifier "$RDS_ID" --region $AWS_REGION --no-cli-pager > /dev/null 2>&1 || true
                echo -e "${GREEN}✓ RDS starting (takes ~5-10 minutes)${NC}"
                echo -e "${BLUE}   Waiting for RDS to be available...${NC}"
                # Wait for RDS to be available (with timeout)
                WAIT_COUNT=0
                MAX_WAIT=60  # 10 minutes max
                while [ $WAIT_COUNT -lt $MAX_WAIT ]; do
                    RDS_STATUS=$(get_rds_status)
                    if [ "$RDS_STATUS" == "available" ]; then
                        echo -e "${GREEN}✓ RDS is available${NC}"
                        break
                    fi
                    echo -e "${BLUE}   RDS status: $RDS_STATUS (waiting...)${NC}"
                    sleep 10
                    WAIT_COUNT=$((WAIT_COUNT + 1))
                done
            elif [ "$RDS_STATUS" == "available" ]; then
                echo -e "${GREEN}✓ RDS already running${NC}"
            else
                echo -e "${YELLOW}⚠ RDS status: $RDS_STATUS${NC}"
            fi
        else
            echo -e "${YELLOW}⚠ No RDS instance found${NC}"
        fi
        
        # Start ECS
        echo -e "${BLUE}[2/2] Scaling up ECS to 1...${NC}"
        SERVICE_ARN=$(get_service_arn)
        if [ -n "$SERVICE_ARN" ] && [ "$SERVICE_ARN" != "None" ]; then
            aws ecs update-service --cluster $ECS_CLUSTER --service $SERVICE_ARN --desired-count 1 --region $AWS_REGION --no-cli-pager > /dev/null
            echo -e "${GREEN}✓ ECS scaling up (takes ~1-2 minutes)${NC}"
        else
            echo -e "${YELLOW}⚠ No ECS service found${NC}"
        fi
        
        echo ""
        echo -e "${GREEN}☀️ Wake complete!${NC}"
        echo -e "Check status: ${YELLOW}./deploy_platform.sh status${NC}"
        
        # Show URL
        ALB_URL=$(get_alb_url)
        if [ -n "$ALB_URL" ]; then
            echo -e "App URL: ${GREEN}http://$ALB_URL${NC}"
        fi
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
        echo "  deploy         - Full deploy (default, uses Docker cache)"
        echo "  deploy-nocache - Full deploy without Docker cache"
        echo "  status         - Check ECS service status"
        echo "  logs           - View recent CloudWatch logs"
        echo "  open           - Open app in browser"
        echo ""
        echo "Cost management:"
        echo "  stop           - Scale ECS to 0 (quick, saves ~\$10/mo)"
        echo "  start          - Scale ECS to 1"
        echo "  hibernate      - Stop ECS + RDS (saves ~\$25/mo, RDS restarts after 7 days)"
        echo "  wake           - Start RDS + ECS (takes ~5-10 min for RDS)"
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
