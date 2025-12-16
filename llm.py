from langchain.chat_models import init_chat_model
import boto3
import os
from dotenv import load_dotenv

load_dotenv()

UNSUPPORTED_ON_DEMAND_MODEL_IDS = {
    # Bedrock currently requires an inference profile for this model.
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
}

def _normalize_aws_env() -> str:
    region = (
        os.getenv("AWS_DEFAULT_REGION")
        or os.getenv("AWS_REGION")
        or os.getenv("REGION_NAME")
        or "us-east-1"
    )
    os.environ.setdefault("AWS_DEFAULT_REGION", region)
    os.environ.setdefault("AWS_REGION", region)
    os.environ.setdefault("AWS_REGION_NAME", region)

    if not os.getenv("AWS_SECRET_ACCESS_KEY") and os.getenv("AWS_SECRET_KEY"):
        os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ["AWS_SECRET_KEY"]

    return region


def _resolve_bedrock_model_id() -> str:
    inference_profile = (
        os.getenv("BEDROCK_INFERENCE_PROFILE_ARN")
        or os.getenv("BEDROCK_INFERENCE_PROFILE_ID")
        or os.getenv("INFERENCE_PROFILE_ARN")
        or os.getenv("INFERENCE_PROFILE_ID")
    )
    if inference_profile:
        return inference_profile

    configured = os.getenv("CHAT_MODEL_ID") or ""
    if configured in UNSUPPORTED_ON_DEMAND_MODEL_IDS:
        return os.getenv("FALLBACK_CHAT_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
    return configured or os.getenv("FALLBACK_CHAT_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")


_normalize_aws_env()
CHAT_MODEL_ID = _resolve_bedrock_model_id()
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_KEY")
AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION")

llm = init_chat_model(
    CHAT_MODEL_ID,
    model_provider="bedrock_converse",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=AWS_DEFAULT_REGION,
    # temperature=BEDROCK_TEMPERATURE,
    # max_tokens=BEDROCK_MAX_TOKENS,
    # top_p=BEDROCK_TOP_P,
)