# Wizelit CDK Infrastructure

AWS CDK stack for deploying the Wizelit Chainlit Hub platform.

## Architecture

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
│  │  │  │  RDS Postgres│        │  ElastiCache     │    │ ││
│  │  │  │  (db.t3.micro)│        │  Redis           │    │ ││
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

## Prerequisites

1. **AWS CLI** configured with credentials
2. **Python 3.11+**
3. **Docker Desktop** (for building images)
4. **AWS CDK CLI**: `npm install -g aws-cdk`

## Initial Setup (First Time Only)

```bash
cd cdk

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Bootstrap CDK (one-time per AWS account/region)
cdk bootstrap aws://ACCOUNT_ID/ap-southeast-2
```

## Infrastructure Deployment (CDK)

```bash
cd cdk

# Preview changes
cdk diff

# Deploy infrastructure
cdk deploy

# Destroy (careful - removes all resources!)
cdk destroy
```

## Application Deployment (Manual)

After infrastructure is deployed, use the deployment script from the project root:

```bash
# From wizelit/ directory (not cdk/)
cd ..

# Full deployment (build, push, deploy)
./deploy_platform.sh

# Deploy without Docker cache (clean rebuild)
./deploy_platform.sh deploy-nocache

# Or individual commands:
./deploy_platform.sh status   # Check service status
./deploy_platform.sh logs     # View CloudWatch logs
./deploy_platform.sh open     # Open app in browser
./deploy_platform.sh stop     # Scale to 0 (save costs)
./deploy_platform.sh start    # Scale to 1
```

### When to Use `deploy-nocache`

Use `deploy-nocache` when:
- **Dependencies changed** - Updated `pyproject.toml` or `uv.lock`
- **Base image updated** - Need to pull latest Python/base image
- **Build issues** - Cached layers causing unexpected behavior
- **Clean rebuild needed** - After significant code changes

Regular `deploy` (with cache) is faster and sufficient for most code changes.

### Manual Deployment Steps (if script fails)

```bash
# 1. Login to ECR
aws ecr get-login-password --region ap-southeast-2 | \
  docker login --username AWS --password-stdin ACCOUNT_ID.dkr.ecr.ap-southeast-2.amazonaws.com

# 2. Build Docker image for AMD64 (required for Fargate)
docker build --platform linux/amd64 -t wizelit .

# 3. Tag image
docker tag wizelit:latest ACCOUNT_ID.dkr.ecr.ap-southeast-2.amazonaws.com/wizelit:latest

# 4. Push to ECR
docker push ACCOUNT_ID.dkr.ecr.ap-southeast-2.amazonaws.com/wizelit:latest

# 5. Force new ECS deployment
aws ecs update-service \
  --cluster wizelit-cluster \
  --service SERVICE_NAME \
  --force-new-deployment \
  --region ap-southeast-2
```

## Helper Scripts

Located in `scripts/` directory:

| Script | Description |
|--------|-------------|
| `aws-scale-down.sh` | Scale ECS to 0 tasks (save Fargate costs) |
| `aws-scale-up.sh` | Scale ECS to 1 task |
| `aws-status.sh` | Check deployment status |

**Note:** These are convenience wrappers. The same functionality is available via `./deploy_platform.sh stop/start/status`.

## Stack Outputs

After deployment, you'll get:

| Output | Description |
|--------|-------------|
| `AlbDnsName` | Application URL (use this to access Wizelit) |
| `EcrRepositoryUri` | ECR repo for Docker images |
| `GithubActionsRoleArn` | IAM role ARN for GitHub Actions |
| `ClusterName` | ECS cluster name |
| `ServiceName` | ECS service name |
| `DbSecretArn` | Database credentials in Secrets Manager |
| `AppSecretArn` | App secrets (configure in AWS Console) |

## GitHub Actions CI/CD Setup

1. Copy `GithubActionsRoleArn` from stack outputs
2. Add to your GitHub repository secrets:
   - `AWS_ROLE_ARN`: The role ARN from outputs
   - `AWS_REGION`: `ap-southeast-2`
3. Update the role in `wizelit_stack.py` to match your repo:

```python
"token.actions.githubusercontent.com:sub": "repo:YOUR_ORG/YOUR_REPO:ref:refs/heads/main"
```

## Configure Secrets

After deployment, update secrets in AWS Console:

### Database Secret (`wizelit/db-credentials`)
Automatically generated - no action needed.

### App Secret (`wizelit/app-secrets`)
Update in AWS Secrets Manager console:

```json
{
  "CHAINLIT_AUTH_SECRET": "generate-a-long-random-string",
  "OAUTH_GOOGLE_CLIENT_ID": "optional-google-oauth-client-id",
  "OAUTH_GOOGLE_CLIENT_SECRET": "optional-google-oauth-secret"
}
```

## Multi-User Support

The deployed Wizelit app supports multiple concurrent users with isolated MCP connections:

- Each user's MCP servers are stored separately (keyed by OAuth email or session ID)
- User A adding/removing servers doesn't affect User B
- Tool metadata is loaded per-user for proper response handling

This requires OAuth authentication for best isolation. Without OAuth, session IDs are used which may be less reliable.

## Connecting to MCP Servers

The deployed Chainlit app can connect to MCP servers anywhere (local via ngrok, cloud, etc.).

### Local Development with ngrok
```bash
# Start local MCP server
cd ../wizelit-mcp && make refactoring-agent

# Expose via ngrok
ngrok http 1337

# Add the ngrok URL in Chainlit UI
```

### Remote MCP Servers
Configure MCP server URLs via the Chainlit UI's MCP panel.

## Cost Optimization

This stack is optimized for development:

| Resource | Size | Monthly Estimate* |
|----------|------|-------------------|
| RDS PostgreSQL | db.t3.micro | ~$15 (free tier eligible) |
| ElastiCache Redis | cache.t3.micro | ~$12 |
| ECS Fargate | 0.25 vCPU / 512MB | ~$10 |
| ALB | Minimal traffic | ~$18 |
| **Total** | | **~$55/month** |

*Estimates assume minimal usage. Free tier can reduce costs significantly.

### Cost Saving Tips

```bash
# Scale down when not in use
./deploy_platform.sh stop

# Scale up when needed
./deploy_platform.sh start
```

## Troubleshooting

### Task fails to start
Check ECS task logs in CloudWatch `/ecs/wizelit`:
```bash
./deploy_platform.sh logs
```

### Cannot connect to database
- Verify security group rules allow ECS → RDS
- Check Secrets Manager for credentials

### MCP tools not appearing
- Ensure MCP server is accessible from your browser
- Check browser console for connection errors
- Verify `response_handling` metadata is set in MCP tool decorators

### GitHub Actions deployment fails
- Verify OIDC provider trust policy
- Check role ARN in GitHub secrets
- Ensure repo name matches in trust policy

### Health check failing
- Container might still be starting (wait 2-3 minutes)
- Check `/health` endpoint returns 200
- View container logs for errors

## Environment Variables

The ECS task uses these environment variables (configured in CDK):

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string (from Secrets Manager) |
| `REDIS_URL` | ElastiCache Redis endpoint |
| `ENABLE_LOG_STREAMING` | Enable Redis streaming (`true`/`false`) |
| `CHAINLIT_AUTH_SECRET` | Auth secret for Chainlit |
| `AWS_REGION` | AWS region for Bedrock |

## Useful Commands

```bash
# CDK commands
cdk list        # List stacks
cdk diff        # Show differences
cdk synth       # Synthesize to cdk.out
cdk deploy      # Deploy stack
cdk destroy     # Destroy stack (careful!)

# Deployment commands
./deploy_platform.sh                # Full deploy (with cache)
./deploy_platform.sh deploy-nocache # Full deploy (clean rebuild)
./deploy_platform.sh status         # Check status
./deploy_platform.sh logs           # View logs
./deploy_platform.sh stop           # Scale down
./deploy_platform.sh start          # Scale up
./deploy_platform.sh open           # Open in browser
```
