import json
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
        self._sessions = {}
        self._tool_sessions = {}

    async def ensure_ready(self):
        if self._graph is not None:
            return

        tools_all = []

        async def connect_and_load(label: str, url: str):
            print(f"🔌 [Agent] Connecting to {label} at {url} ...")
            sse = await self._exit_stack.enter_async_context(
                sse_client(url=url, timeout=600.0)
            )
            read_stream, write_stream = sse
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            tools = await load_mcp_tools(session)
            if not tools:
                raise RuntimeError(f"❌ Connected to {url}, but found 0 tools!")
            print(f"✅ [Agent] Tools Loaded from {label}: {[t.name for t in tools]}")
            # Track which session owns which tool name for direct calls
            for t in tools:
                self._tool_sessions[t.name] = session
            # Keep session for potential future use
            self._sessions[label] = session
            tools_all.extend(tools)

        try:
            with open("config/agents.json") as f:
                agents_config = json.load(f)

            for agent in agents_config.values():
                await connect_and_load(agent["name"], agent["url"])

            # Bedrock LLM
            region = normalize_aws_env(default_region="us-east-1")
            model_id = resolve_bedrock_model_id()
            llm = ChatBedrock(
                model_id=model_id,
                model_kwargs={"temperature": 0},
                region_name=region,
            )

            self._graph = build_graph(llm=llm, tools=tools_all)

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
        if not self._tool_sessions:
            await self.ensure_ready()
        session = self._tool_sessions.get(name)
        if not session:
            raise ValueError(f"Tool '{name}' is not registered in any session")
        return await session.call_tool(name, arguments)

agent_runtime = AgentRuntime()
