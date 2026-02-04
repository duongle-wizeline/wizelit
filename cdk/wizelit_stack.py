"""
Wizelit CDK Stack - Unified infrastructure for Chainlit Hub
"""
import json
from constructs import Construct
import aws_cdk as cdk
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecr as ecr,
    aws_elasticloadbalancingv2 as elbv2,
    aws_rds as rds,
    aws_elasticache as elasticache,
    aws_secretsmanager as secretsmanager,
    aws_iam as iam,
    aws_logs as logs,
)


class WizelitStack(Stack):
    """
    Unified CDK Stack for Wizelit Chainlit Hub.

    Includes:
    - VPC with public/private subnets
    - RDS PostgreSQL (free tier)
    - ElastiCache Redis
    - ECS Fargate service
    - Application Load Balancer
    - ECR Repository
    - GitHub OIDC for CI/CD
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ======================================================================
        # VPC - Cost optimized (no NAT Gateway for dev)
        # ======================================================================
        vpc = ec2.Vpc(
            self,
            "WizelitVpc",
            max_azs=2,
            nat_gateways=0,  # Cost saving: No NAT Gateway for dev
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    cidr_mask=24,
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                ),
                ec2.SubnetConfiguration(
                    cidr_mask=24,
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                ),
            ],
        )

        # ======================================================================
        # Security Groups
        # ======================================================================
        alb_security_group = ec2.SecurityGroup(
            self,
            "AlbSecurityGroup",
            vpc=vpc,
            description="Security group for ALB",
            allow_all_outbound=True,
        )
        alb_security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(80),
            "Allow HTTP",
        )
        alb_security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "Allow HTTPS",
        )

        ecs_security_group = ec2.SecurityGroup(
            self,
            "EcsSecurityGroup",
            vpc=vpc,
            description="Security group for ECS tasks",
            allow_all_outbound=True,
        )
        ecs_security_group.add_ingress_rule(
            alb_security_group,
            ec2.Port.tcp(8000),
            "Allow from ALB",
        )

        db_security_group = ec2.SecurityGroup(
            self,
            "DbSecurityGroup",
            vpc=vpc,
            description="Security group for RDS",
            allow_all_outbound=False,
        )
        db_security_group.add_ingress_rule(
            ecs_security_group,
            ec2.Port.tcp(5432),
            "Allow PostgreSQL from ECS",
        )

        redis_security_group = ec2.SecurityGroup(
            self,
            "RedisSecurityGroup",
            vpc=vpc,
            description="Security group for Redis",
            allow_all_outbound=False,
        )
        redis_security_group.add_ingress_rule(
            ecs_security_group,
            ec2.Port.tcp(6379),
            "Allow Redis from ECS",
        )

        # ======================================================================
        # Secrets Manager - Database credentials
        # ======================================================================
        db_secret = secretsmanager.Secret(
            self,
            "DbSecret",
            secret_name="wizelit/db-credentials",
            description="Wizelit PostgreSQL database credentials",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({"username": "wizelit"}),
                generate_string_key="password",
                exclude_punctuation=True,
                password_length=32,
            ),
        )

        # Application secrets (Chainlit, OAuth - optional)
        app_secret = secretsmanager.Secret(
            self,
            "AppSecret",
            secret_name="wizelit/app-secrets",
            description="Wizelit application secrets",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template=json.dumps({
                    "CHAINLIT_AUTH_SECRET": "change-me-in-console",
                    "OAUTH_GOOGLE_CLIENT_ID": "",
                    "OAUTH_GOOGLE_CLIENT_SECRET": "",
                }),
                generate_string_key="dummy",
            ),
        )

        # ======================================================================
        # RDS PostgreSQL - Free Tier (db.t3.micro)
        # ======================================================================
        database = rds.DatabaseInstance(
            self,
            "WizelitDb",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3,
                ec2.InstanceSize.MICRO,
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            security_groups=[db_security_group],
            credentials=rds.Credentials.from_secret(db_secret),
            database_name="wizelit",
            allocated_storage=20,
            max_allocated_storage=100,
            storage_encrypted=True,
            multi_az=False,  # Cost saving for dev
            deletion_protection=False,
            removal_policy=RemovalPolicy.DESTROY,
            backup_retention=Duration.days(7),
            publicly_accessible=False,
        )

        # ======================================================================
        # ElastiCache Redis - cache.t3.micro
        # ======================================================================
        redis_subnet_group = elasticache.CfnSubnetGroup(
            self,
            "RedisSubnetGroup",
            description="Subnet group for Wizelit Redis",
            subnet_ids=vpc.select_subnets(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ).subnet_ids,
            cache_subnet_group_name="wizelit-redis-subnet-group",
        )

        redis_cluster = elasticache.CfnCacheCluster(
            self,
            "RedisCluster",
            cache_node_type="cache.t3.micro",
            engine="redis",
            num_cache_nodes=1,
            cluster_name="wizelit-redis",
            vpc_security_group_ids=[redis_security_group.security_group_id],
            cache_subnet_group_name=redis_subnet_group.cache_subnet_group_name,
        )
        redis_cluster.add_dependency(redis_subnet_group)

        # ======================================================================
        # ECR Repository
        # ======================================================================
        ecr_repository = ecr.Repository(
            self,
            "WizelitEcr",
            repository_name="wizelit",
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    max_image_count=10,
                    description="Keep only 10 images",
                ),
            ],
        )

        # ======================================================================
        # ECS Cluster
        # ======================================================================
        cluster = ecs.Cluster(
            self,
            "WizelitCluster",
            vpc=vpc,
            cluster_name="wizelit-cluster",
            container_insights=False,  # Cost saving for dev
        )

        # ======================================================================
        # IAM Role for ECS Task (Bedrock access)
        # ======================================================================
        task_role = iam.Role(
            self,
            "EcsTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Role for Wizelit ECS tasks",
        )

        # Add Bedrock permissions
        task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
            )
        )

        # Add Secrets Manager read permissions
        task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[db_secret.secret_arn, app_secret.secret_arn],
            )
        )

        # ======================================================================
        # ECS Task Definition
        # ======================================================================
        task_definition = ecs.FargateTaskDefinition(
            self,
            "WizelitTaskDef",
            memory_limit_mib=512,
            cpu=256,
            task_role=task_role,
        )

        log_group = logs.LogGroup(
            self,
            "WizelitLogs",
            log_group_name="/ecs/wizelit",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        container = task_definition.add_container(
            "wizelit",
            image=ecs.ContainerImage.from_ecr_repository(ecr_repository, "latest"),
            logging=ecs.LogDrivers.aws_logs(
                log_group=log_group,
                stream_prefix="wizelit",
            ),
            environment={
                # Database
                "POSTGRES_HOST": database.instance_endpoint.hostname,
                "POSTGRES_PORT": "5432",
                "POSTGRES_DB": "wizelit",
                # Redis
                "REDIS_URL": f"redis://{redis_cluster.attr_redis_endpoint_address}:{redis_cluster.attr_redis_endpoint_port}",
                # Disabled for now - only works when MCP servers share the same Redis
                # Enable when MCP servers are deployed to AWS alongside Chainlit
                "ENABLE_LOG_STREAMING": "false",
                # AWS
                "AWS_REGION": self.region,
                # App config
                "TASK_TIMEOUT": "1200",
                "MAX_HISTORY_TURNS": "10",
                "LOG_STREAM_TIMEOUT_SECONDS": "300",
                # Model3699

                "CHAT_MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0",
            },
            secrets={
                "POSTGRES_USER": ecs.Secret.from_secrets_manager(db_secret, "username"),
                "POSTGRES_PASSWORD": ecs.Secret.from_secrets_manager(db_secret, "password"),
                "CHAINLIT_AUTH_SECRET": ecs.Secret.from_secrets_manager(
                    app_secret, "CHAINLIT_AUTH_SECRET"
                ),
            },
            port_mappings=[ecs.PortMapping(container_port=8000)],
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )

        # ======================================================================
        # Application Load Balancer
        # ======================================================================
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "WizelitAlb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_security_group,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        listener = alb.add_listener(
            "HttpListener",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
        )

        # ======================================================================
        # ECS Service
        # ======================================================================
        ecs_service = ecs.FargateService(
            self,
            "WizelitService",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=1,  # Running task count
            assign_public_ip=True,  # Required since no NAT Gateway
            security_groups=[ecs_security_group],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )

        listener.add_targets(
            "WizelitTarget",
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[ecs_service],
            health_check=elbv2.HealthCheck(
                path="/health",
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
        )

        # ======================================================================
        # GitHub OIDC Provider for CI/CD
        # Imports existing OIDC provider (account-level, already exists)
        # ======================================================================
        github_oidc_provider_arn = f"arn:aws:iam::{self.account}:oidc-provider/token.actions.githubusercontent.com"

        github_oidc_provider = iam.OpenIdConnectProvider.from_open_id_connect_provider_arn(
            self,
            "GithubOidcProvider",
            open_id_connect_provider_arn=github_oidc_provider_arn,
        )

        # Role for GitHub Actions
        github_actions_role = iam.Role(
            self,
            "GithubActionsRole",
            assumed_by=iam.FederatedPrincipal(
                github_oidc_provider.open_id_connect_provider_arn,
                conditions={
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    },
                    "StringLike": {
                        # GitHub repo: https://github.com/duongle-wizeline/wizelit
                        "token.actions.githubusercontent.com:sub": "repo:duongle-wizeline/wizelit:*",
                    },
                },
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
            description="Role for GitHub Actions to deploy Wizelit",
            max_session_duration=Duration.hours(1),
        )

        # ECR permissions for GitHub Actions
        github_actions_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:PutImage",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                ],
                resources=["*"],
            )
        )

        # ECS permissions for GitHub Actions
        github_actions_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "ecs:UpdateService",
                    "ecs:DescribeServices",
                    "ecs:DescribeTaskDefinition",
                    "ecs:RegisterTaskDefinition",
                    "ecs:ListTasks",
                    "ecs:DescribeTasks",
                ],
                resources=["*"],
            )
        )

        # IAM PassRole for GitHub Actions
        github_actions_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["iam:PassRole"],
                resources=[
                    task_role.role_arn,
                    task_definition.execution_role.role_arn,
                ],
            )
        )

        # ======================================================================
        # Outputs
        # ======================================================================
        CfnOutput(
            self,
            "AlbDnsName",
            value=alb.load_balancer_dns_name,
            description="Application Load Balancer DNS Name",
            export_name="WizelitAlbDnsName",
        )

        CfnOutput(
            self,
            "EcrRepositoryUri",
            value=ecr_repository.repository_uri,
            description="ECR Repository URI",
            export_name="WizelitEcrRepositoryUri",
        )

        CfnOutput(
            self,
            "GithubActionsRoleArn",
            value=github_actions_role.role_arn,
            description="GitHub Actions Role ARN (add to GitHub secrets as AWS_ROLE_ARN)",
            export_name="WizelitGithubActionsRoleArn",
        )

        CfnOutput(
            self,
            "ClusterName",
            value=cluster.cluster_name,
            description="ECS Cluster Name",
            export_name="WizelitClusterName",
        )

        CfnOutput(
            self,
            "ServiceName",
            value=ecs_service.service_name,
            description="ECS Service Name",
            export_name="WizelitServiceName",
        )

        CfnOutput(
            self,
            "DbSecretArn",
            value=db_secret.secret_arn,
            description="Database Secret ARN (check console for credentials)",
        )

        CfnOutput(
            self,
            "AppSecretArn",
            value=app_secret.secret_arn,
            description="App Secret ARN (configure Chainlit/OAuth in console)",
        )
