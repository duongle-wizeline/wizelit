import os
from dotenv import load_dotenv

load_dotenv()

import asyncio
from contextlib import AsyncExitStack
from langchain_aws import ChatBedrock
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from langchain_mcp_adapters.tools import load_mcp_tools
from graph import build_graph

UNSUPPORTED_ON_DEMAND_MODEL_IDS = {
    # Bedrock currently requires an inference profile for this model.
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
}

def _normalize_aws_env() -> str:
    """
    Normalize environment variables so different libraries pick them up consistently.
    Returns the resolved AWS region name.
    """
    # Region: support both AWS_* conventions and this repo's REGION_NAME.
    region = (
        os.getenv("AWS_DEFAULT_REGION")
        or os.getenv("AWS_REGION")
        or os.getenv("REGION_NAME")
        or "us-east-1"
    )
    os.environ.setdefault("AWS_DEFAULT_REGION", region)
    os.environ.setdefault("AWS_REGION", region)
    os.environ.setdefault("AWS_REGION_NAME", region)

    # Credentials: some setups use AWS_SECRET_KEY instead of AWS_SECRET_ACCESS_KEY.
    if not os.getenv("AWS_SECRET_ACCESS_KEY") and os.getenv("AWS_SECRET_KEY"):
        os.environ["AWS_SECRET_ACCESS_KEY"] = os.environ["AWS_SECRET_KEY"]

    return region


def _resolve_bedrock_model_id() -> str:
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
    if configured in UNSUPPORTED_ON_DEMAND_MODEL_IDS:
        # Known-good on-demand default for broad compatibility.
        return os.getenv("FALLBACK_CHAT_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")

    return configured or os.getenv("FALLBACK_CHAT_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")

class AgentRuntime:
    def __init__(self):
        self._graph = None
        self._exit_stack = AsyncExitStack() 
        self._session = None

    async def ensure_ready(self):
        if self._graph is not None:
            return

        url = "http://127.0.0.1:1337/sse"
        print(f"🔌 [Agent] Connecting to Refactoring Agent at {url} ...")
        
        try:
            # sse is a tuple: (read_stream, write_stream)
            sse = await self._exit_stack.enter_async_context(
                sse_client(url=url, timeout=600.0)
            )
            
            # Unpack the streams explicitly for clarity
            read_stream, write_stream = sse

            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await self._session.initialize()
            
            tools = await load_mcp_tools(self._session)
            if not tools:
                raise RuntimeError(f"❌ Connected to {url}, but found 0 tools!")
                
            print(f"✅ [Agent] Tools Loaded: {[t.name for t in tools]}")
            
            # Bedrock LLM
            region = _normalize_aws_env()
            model_id = _resolve_bedrock_model_id()
            llm = ChatBedrock(
                model_id=model_id,
                model_kwargs={"temperature": 0},
                region_name=region,  # Explicitly use the resolved region
            )
            
            self._graph = build_graph(llm=llm, tools=tools)
            
        except Exception as e:
            print(f"❌ [Agent] Connection Failed: {e}")
            await self._exit_stack.aclose()
            raise e

    async def get_graph(self):
        if self._graph is None:
            await self.ensure_ready()
        return self._graph
    
    async def graph_to_mermaid(self) -> str:
        """Convert the graph to a Mermaid string."""
        computed_graph = await self.get_graph()
        return computed_graph.get_graph().draw_mermaid()
    
    # Allow calling tools directly (for polling)
    async def call_tool(self, name: str, arguments: dict):
        if self._session is None:
            await self.ensure_ready()
        return await self._session.call_tool(name, arguments)

agent_runtime = AgentRuntime()