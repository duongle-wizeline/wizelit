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
            llm = ChatBedrock(
                model_id=os.getenv("CHAT_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0"),
                model_kwargs={"temperature": 0},
                region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1") # Explicitly use the region
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
    
    # Allow calling tools directly (for polling)
    async def call_tool(self, name: str, arguments: dict):
        if self._session is None:
            await self.ensure_ready()
        return await self._session.call_tool(name, arguments)

agent_runtime = AgentRuntime()
