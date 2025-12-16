from langchain.chat_models import init_chat_model
import boto3
import os
from dotenv import load_dotenv

load_dotenv()

from utils.bedrock_config import normalize_aws_env, resolve_bedrock_model_id


normalize_aws_env(default_region="us-east-1")
CHAT_MODEL_ID = resolve_bedrock_model_id()
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