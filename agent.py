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
from utils.bedrock_config import normalize_aws_env, resolve_bedrock_model_id


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
            region = normalize_aws_env(default_region="us-east-1")
            model_id = resolve_bedrock_model_id()
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