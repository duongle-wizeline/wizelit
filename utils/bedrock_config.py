"""
Shared AWS/Bedrock configuration helpers.

Several entrypoints in this repo need to:
- Normalize AWS environment variables so different libraries agree
- Resolve the Bedrock model identifier, optionally via an inference profile
"""

from __future__ import annotations

import os
from typing import Final, Set


# Bedrock currently requires an inference profile for these models.
UNSUPPORTED_ON_DEMAND_MODEL_IDS: Final[Set[str]] = {
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
}


def normalize_aws_env(*, default_region: str = "us-east-1") -> str:
    """
    Normalize environment variables so different libraries pick them up consistently.
    Returns the resolved AWS region name.
    """
    # Region: support both AWS_* conventions and this repo's REGION_NAME.
    region = (
        os.getenv("AWS_DEFAULT_REGION")
        or os.getenv("AWS_REGION")
        or os.getenv("REGION_NAME")
        or default_region
    )
    os.environ.setdefault("AWS_DEFAULT_REGION", region)
    os.environ.setdefault("AWS_REGION", region)
    os.environ.setdefault("AWS_REGION_NAME", region)

    # Credentials: some setups use AWS_SECRET_KEY instead of AWS_SECRET_ACCESS_KEY.
    if not os.getenv("AWS_SECRET_ACCESS_KEY") and os.getenv("AWS_SECRET_KEY"):
        os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ["AWS_SECRET_KEY"]

    return region


def resolve_bedrock_model_id(
    *,
    fallback_model_id: str = "anthropic.claude-3-haiku-20240307-v1:0",
) -> str:
    """
    Resolve the Bedrock model identifier to use.

    - If an inference profile ARN/ID is provided, use it.
    - Otherwise use CHAT_MODEL_ID unless it is known to be unsupported for on-demand,
      in which case fall back to a safe on-demand model.
    """
    inference_profile = (
        os.getenv("BEDROCK_INFERENCE_PROFILE_ARN")
        or os.getenv("BEDROCK_INFERENCE_PROFILE_ID")
        or os.getenv("INFERENCE_PROFILE_ARN")
        or os.getenv("INFERENCE_PROFILE_ID")
    )
    if inference_profile:
        return inference_profile

    configured = os.getenv("CHAT_MODEL_ID") or ""
    env_fallback = os.getenv("FALLBACK_CHAT_MODEL_ID", fallback_model_id)

    if configured in UNSUPPORTED_ON_DEMAND_MODEL_IDS:
        return env_fallback

    return configured or env_fallback