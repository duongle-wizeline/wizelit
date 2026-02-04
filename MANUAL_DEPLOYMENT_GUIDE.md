# Wizelit Manual Deployment Guide

Complete guide to deploy Wizelit from scratch to AWS.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [AWS Account Setup](#aws-account-setup)
3. [CDK Infrastructure Deployment](#cdk-infrastructure-deployment)
4. [Application Deployment](#application-deployment)
5. [Configure Secrets](#configure-secrets)
6. [Google OAuth Setup](#google-oauth-setup)
7. [GitHub Actions CI/CD Setup](#github-actions-cicd-setup)
8. [Cost Management](#cost-management)
9. [Custom Domain Setup](#custom-domain-setup)
10. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Tools

```bash
# 1. AWS CLI (v2)
brew install awscli
aws --version  # Should be 2.x

# 2. AWS CDK CLI
npm install -g aws-cdk
cdk --version  # Should be 2.x

# 3. Docker Desktop
# Download from https://www.docker.com/products/docker-desktop
docker --version

# 4. Python 3.11+
python3 --version  # Should be 3.11+

# 5. uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### AWS Credentials

Configure AWS CLI with your credentials:

```bash
aws configure
# Enter:
#   AWS Access Key ID
#   AWS Secret Access Key
#   Default region: ap-southeast-2
#   Default output format: json

# Verify credentials
aws sts get-caller-identity
```

---

## AWS Account Setup

### 1. Bootstrap CDK (One-time per AWS account/region)

```bash
# Get your AWS account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Bootstrap CDK
cdk bootstrap aws://$ACCOUNT_ID/ap-southeast-2
```

### 2. Create Environment File

```bash
cd wizelit

# Copy template
cp .env.template .env

# Edit with your values
nano .env
```

Required `.env` values for deployment:

```bash
# AWS Configuration
AWS_REGION=ap-southeast-2
CDK_DEFAULT_ACCOUNT=YOUR_ACCOUNT_ID
CDK_DEFAULT_REGION=ap-southeast-2

# Bedrock Model (ensure you have access in your AWS account)
CHAT_MODEL_ID=anthropic.claude-3-haiku-20240307-v1:0
```

---

## CDK Infrastructure Deployment

### 1. Setup CDK Virtual Environment

```bash
cd wizelit/cdk

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Install CDK dependencies
pip install -r requirements.txt
```

### 2. Preview Infrastructure Changes

```bash
# See what will be created
cdk diff
```

### 3. Deploy Infrastructure

```bash
# Deploy the stack
cdk deploy

# This creates:
#   - VPC with public/private subnets
#   - Application Load Balancer (ALB)
#   - ECS Fargate cluster
#   - RDS PostgreSQL database
#   - ElastiCache Redis
#   - ECR repository
#   - Secrets Manager secrets
#   - IAM roles for GitHub Actions
```

**⚠️ Important:** Note the outputs after deployment:

| Output | Description |
|--------|-------------|
| `AlbDnsName` | Your application URL |
| `EcrRepositoryUri` | ECR repo for Docker images |
| `GithubActionsRoleArn` | IAM role for GitHub Actions |
| `DbSecretArn` | Database credentials location |
| `AppSecretArn` | App secrets location |

**Note:** The ECS service is created with `desired_count=0` to prevent deployment circuit breaker issues. After deploying the application (see next section), you'll need to run `./deploy_platform.sh start` to actually start the service.

### 4. Common CDK Commands

```bash
cdk list        # List stacks
cdk diff        # Show changes
cdk synth       # Generate CloudFormation template
cdk deploy      # Deploy/update stack
cdk destroy     # Delete stack (careful!)
```

---

## Application Deployment

After CDK infrastructure is deployed, deploy the application:

### 1. First-time Deployment

```bash
cd wizelit  # Project root, not cdk/

# Step 1: Build and push Docker image to ECR
./deploy_platform.sh deploy

# Step 2: Start the ECS service (required after first deploy!)
./deploy_platform.sh start
```

**⚠️ Important:** The ECS service is created with `desired_count=0` to prevent deployment issues. You **must** run `./deploy_platform.sh start` after the first deployment to actually start the application.

Wait 1-2 minutes for the service to start, then verify:

```bash
# Check if service is running
./deploy_platform.sh status

# Open in browser
./deploy_platform.sh open
```

### 2. Subsequent Deployments

For subsequent deployments (after first-time setup), you only need:

```bash
# Just deploy - service is already running
./deploy_platform.sh deploy
```

### 3. Deployment Options

```bash
# Standard deploy (uses Docker cache - faster)
./deploy_platform.sh deploy

# Clean rebuild (no cache - use when dependencies change)
./deploy_platform.sh deploy-nocache

# Check deployment status
./deploy_platform.sh status

# View application logs
./deploy_platform.sh logs

# Open app in browser
./deploy_platform.sh open
```

### 4. Manual Deployment Steps (if script fails)

```bash
# Get account ID and region
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=ap-southeast-2

# 1. Login to ECR
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

# 2. Build Docker image for AMD64 (required for Fargate)
docker build --platform linux/amd64 -t wizelit .

# 3. Tag image
docker tag wizelit:latest $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/wizelit:latest

# 4. Push to ECR
docker push $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/wizelit:latest

# 5. Get ECS service name
SERVICE_NAME=$(aws ecs list-services --cluster wizelit-cluster \
  --query 'serviceArns[0]' --output text | xargs basename)

# 6. Force new ECS deployment
aws ecs update-service \
  --cluster wizelit-cluster \
  --service $SERVICE_NAME \
  --force-new-deployment \
  --region $AWS_REGION
```

---

## Configure Secrets

After deployment, configure secrets in AWS Secrets Manager.

### 1. Generate Chainlit Auth Secret

```bash
# Generate a random secret
openssl rand -hex 32
```

### 2. Update App Secrets in AWS Console

1. Go to AWS Console → Secrets Manager
2. Find secret: `wizelit/app-secrets`
3. Click "Retrieve secret value" → "Edit"
4. Update with:

```json
{
  "CHAINLIT_AUTH_SECRET": "YOUR_GENERATED_SECRET",
  "OAUTH_GOOGLE_CLIENT_ID": "your-google-client-id",
  "OAUTH_GOOGLE_CLIENT_SECRET": "your-google-client-secret"
}
```

### 3. Database Secret

The database secret (`wizelit/db-credentials`) is automatically generated by CDK. No action needed.

---

## Google OAuth Setup

### 1. Create OAuth Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Navigate to: APIs & Services → Credentials
4. Click "Create Credentials" → "OAuth 2.0 Client ID"
5. Application type: "Web application"

### 2. Configure Redirect URIs

Add these **Authorized redirect URIs**:

```
http://YOUR_ALB_DNS_NAME/auth/oauth/google/callback
http://localhost:8000/auth/oauth/google/callback
```

Replace `YOUR_ALB_DNS_NAME` with your ALB URL from CDK outputs.

**Example:**
```
http://wizeli-wizel-abc123.ap-southeast-2.elb.amazonaws.com/auth/oauth/google/callback
```

### 3. Update AWS Secrets

Copy the Client ID and Client Secret to AWS Secrets Manager (see above).

### 4. Redeploy Application

After updating secrets, force a new deployment:

```bash
./deploy_platform.sh deploy
```

---

## GitHub Actions CI/CD Setup

### 1. Get Role ARN from CDK Outputs

```bash
# After cdk deploy, note the GithubActionsRoleArn output
# Example: arn:aws:iam::123456789012:role/wizelit-dev-GithubActionsRole...
```

### 2. Update CDK Stack for Your Repository

Edit `wizelit/cdk/wizelit_stack.py` and update the OIDC trust policy:

```python
# Find this line and update with your GitHub org/repo:
"token.actions.githubusercontent.com:sub": "repo:YOUR_ORG/YOUR_REPO:*",

# Example:
"token.actions.githubusercontent.com:sub": "repo:duongle-wizeline/wizelit:*",
```

Redeploy CDK after this change:

```bash
cd cdk && cdk deploy
```

### 3. Configure GitHub Repository Secrets

Go to your GitHub repo → Settings → Secrets and variables → Actions

Add these secrets:

| Secret Name | Value |
|-------------|-------|
| `AWS_ROLE_ARN` | The `GithubActionsRoleArn` from CDK outputs |
| `AWS_REGION` | `ap-southeast-2` |

### 4. Trigger Deployment

Deployments trigger automatically when:
- Push to `main` branch
- Manual trigger via "Actions" tab → "Deploy to AWS ECS" → "Run workflow"

**Manual trigger options:**
- `no_cache`: Set to `true` for clean rebuild

---

## Cost Management

### Quick Reference

| Command | Stops | Saves | Restart Time |
|---------|-------|-------|--------------|
| `stop` | ECS | ~$10/mo | ~1-2 min |
| `hibernate` | ECS + RDS | ~$25/mo | ~5-10 min |
| `cdk destroy` | Everything | ~$55/mo | ~15-20 min |

### Stop ECS Only (Quick)

```bash
# Stop (scale to 0 tasks)
./deploy_platform.sh stop

# Start (scale to 1 task)
./deploy_platform.sh start
```

### Hibernate (Stop ECS + RDS)

```bash
# Hibernate (stops ECS and RDS)
./deploy_platform.sh hibernate

# Wake (starts RDS then ECS)
./deploy_platform.sh wake
```

**⚠️ Note:** RDS auto-restarts after 7 days (AWS limitation).

### Full Shutdown (cdk destroy)

```bash
cd cdk
cdk destroy
```

**⚠️ Warning:** This deletes everything including:
- Database (data is lost!)
- Load balancer (URL changes on next deploy)
- All infrastructure

### Cost Breakdown

| Resource | Monthly Cost |
|----------|--------------|
| ECS Fargate (0.25 vCPU, 512MB) | ~$10 |
| RDS PostgreSQL (db.t3.micro) | ~$15 |
| ElastiCache Redis (cache.t3.micro) | ~$12 |
| Application Load Balancer | ~$18 |
| **Total (running)** | **~$55** |

---

## Custom Domain Setup

### Option 1: Simple CNAME (HTTP only)

Ask your IT team to add a DNS record:

| Type | Name | Value |
|------|------|-------|
| CNAME | `wizelit` | `YOUR_ALB_DNS_NAME` |

Example: `wizelit.wizeline.com` → `wizeli-wizel-abc123.ap-southeast-2.elb.amazonaws.com`

### Option 2: HTTPS with AWS Certificate Manager

1. **Request certificate:**
```bash
aws acm request-certificate \
  --domain-name wizelit.wizeline.com \
  --validation-method DNS \
  --region ap-southeast-2
```

2. **Add DNS validation record** (provided by ACM)

3. **Update CDK stack** to add HTTPS listener (requires code changes)

4. **Add CNAME record** pointing to ALB

---

## Troubleshooting

### ECS Task Fails to Start

```bash
# Check ECS task logs
./deploy_platform.sh logs

# Or in AWS Console:
# ECS → Clusters → wizelit-cluster → Tasks → Select task → Logs
```

### Health Check Failing

- Wait 2-3 minutes for container to start
- Check if `/health` endpoint returns 200
- Verify security group rules

### Cannot Connect to Database

- Verify ECS security group can access RDS security group
- Check Secrets Manager for correct credentials
- Ensure RDS is not stopped (`./deploy_platform.sh wake`)

### MCP Tools Not Appearing

- Ensure MCP server is accessible from your browser
- Check browser console for connection errors
- Verify MCP server URL in Chainlit UI

### GitHub Actions Deployment Fails

- Verify OIDC provider trust policy matches your repo
- Check role ARN in GitHub secrets
- Ensure repo name matches in `wizelit_stack.py`

### Docker Build Issues

```bash
# Clean rebuild without cache
./deploy_platform.sh deploy-nocache

# Or manually:
docker build --platform linux/amd64 --no-cache -t wizelit .
```

### ALB URL Changed After Redeploy

This happens when you run `cdk destroy` then `cdk deploy`. To avoid:
- Use `cdk deploy` to update (preserves ALB)
- Never run `cdk destroy` unless necessary
- Update Google OAuth redirect URIs with new URL

---

## Quick Reference Commands

```bash
# === CDK (Infrastructure) ===
cd cdk
cdk deploy          # Deploy/update infrastructure
cdk diff            # Preview changes
cdk destroy         # Delete everything

# === Application ===
cd ..  # Back to project root
./deploy_platform.sh deploy         # Deploy app (with cache)
./deploy_platform.sh deploy-nocache # Deploy app (clean build)
./deploy_platform.sh status         # Check ECS status
./deploy_platform.sh logs           # View logs
./deploy_platform.sh open           # Open in browser

# === Cost Management ===
./deploy_platform.sh stop           # Stop ECS (quick)
./deploy_platform.sh start          # Start ECS
./deploy_platform.sh hibernate      # Stop ECS + RDS
./deploy_platform.sh wake           # Start RDS + ECS
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                         AWS Cloud                            │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                      VPC (2 AZs)                        ││
│  │  ┌───────────────────────────────────────────────────┐ ││
│  │  │           Public Subnets                          │ ││
│  │  │  ┌─────────────┐    ┌─────────────────────────┐  │ ││
│  │  │  │     ALB     │    │   ECS Fargate Service   │  │ ││
│  │  │  │   (HTTP)    │───>│   (Chainlit App)        │  │ ││
│  │  │  └─────────────┘    └─────────────────────────┘  │ ││
│  │  └───────────────────────────────────────────────────┘ ││
│  │  ┌───────────────────────────────────────────────────┐ ││
│  │  │         Private Isolated Subnets                  │ ││
│  │  │  ┌─────────────┐         ┌──────────────────┐    │ ││
│  │  │  │  RDS        │         │  ElastiCache     │    │ ││
│  │  │  │  PostgreSQL │         │  Redis           │    │ ││
│  │  │  └─────────────┘         └──────────────────┘    │ ││
│  │  └───────────────────────────────────────────────────┘ ││
│  └─────────────────────────────────────────────────────────┘│
│                                                              │
│  ┌─────────┐  ┌────────────────┐  ┌─────────────────┐      │
│  │   ECR   │  │ Secrets Manager│  │ GitHub OIDC     │      │
│  │ Repo    │  │ (DB + App)     │  │ Provider        │      │
│  └─────────┘  └────────────────┘  └─────────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

---

## Support

For issues or questions:
1. Check the [Troubleshooting](#troubleshooting) section
2. View CloudWatch logs: `./deploy_platform.sh logs`
3. Check ECS task status in AWS Console
