#!/usr/bin/env python3
"""
Wizelit CDK App - Entry Point
"""
import os
import aws_cdk as cdk
from wizelit_stack import WizelitStack

app = cdk.App()

# Get environment from context or use defaults
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT", os.environ.get("AWS_ACCOUNT_ID")),
    region=os.environ.get("CDK_DEFAULT_REGION", "ap-southeast-2"),
)

# Create the unified Wizelit stack
WizelitStack(
    app,
    "WizelitStack",
    env=env,
    description="Wizelit Chainlit Hub - AI Agent Orchestration Platform",
    stack_name="wizelit-dev",
)

# Add tags to all resources
cdk.Tags.of(app).add("Project", "Wizelit")
cdk.Tags.of(app).add("Environment", "dev")
cdk.Tags.of(app).add("ManagedBy", "CDK")

app.synth()
