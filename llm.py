from langchain.chat_models import init_chat_model
import boto3
import os
from dotenv import load_dotenv

load_dotenv()

CHAT_MODEL_ID = os.getenv("CHAT_MODEL_ID")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_KEY")
REGION_NAME = os.getenv("REGION_NAME")

llm = init_chat_model(
    CHAT_MODEL_ID,
    model_provider="bedrock_converse",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_KEY"),
    region_name=REGION_NAME,
    # temperature=BEDROCK_TEMPERATURE,
    # max_tokens=BEDROCK_MAX_TOKENS,
    # top_p=BEDROCK_TOP_P,
)