import asyncio
import uuid
import os
from typing import Dict, Any

# FastMCP
from core.wizelit_agent_wrapper import WizelitAgentWrapper
# Bedrock
from langchain_aws import ChatBedrock
from langchain_core.messages import SystemMessage, HumanMessage

# Initialize FastMCP
mcp = WizelitAgentWrapper("RefactoringCrewAgent", port=1337)

# In-Memory Job Store
JOBS: Dict[str, Dict[str, Any]] = {}

async def _run_refactoring_crew(job_id: str, code: str, instruction: str):
    """
    Refactor code using AWS Bedrock (Claude) in two steps:
    1) Architect-style analysis + plan
    2) Code-only refactor output

    NOTE: This intentionally avoids CrewAI's default LLM loader, which may
    fall back to OpenAI and require OPENAI_API_KEY.
    """
    try:
        job = JOBS[job_id]

        # 1. Initialize the LLM (Bedrock)
        llm = ChatBedrock(
            model_id=os.getenv("CHAT_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0"),
            model_kwargs={"temperature": 0},
            region_name=os.getenv("AWS_DEFAULT_REGION", "ap-southeast-2"),
        )

        def _content(msg) -> str:
            content = getattr(msg, "content", "")
            return content if isinstance(content, str) else str(content)

        job["logs"].append("🧠 Starting analysis the code...")

        analysis_prompt = (
            "Analyze the following Python code according to the user's instruction.\n"
            "Identify the top 3 critical issues (e.g., global state, lack of typing, tight coupling, poor naming).\n"
            "Then propose a short refactoring plan.\n\n"
            f"INSTRUCTION:\n{instruction}\n\n"
            f"CODE:\n{code}\n"
        )

        analysis_msg = await asyncio.to_thread(
            llm.invoke,
            [SystemMessage(content="You are a Senior Software Architect who prioritizes SOLID and clean architecture."),
             HumanMessage(content=analysis_prompt)],
        )
        analysis_text = _content(analysis_msg).strip()
        job["logs"].append("🧩 Analysis completed. Refactoring the code...")

        refactor_prompt = (
            "Refactor the code based on the architect analysis and the instruction.\n"
            "Use Python type hints and (only when appropriate) Pydantic models.\n"
            "Output ONLY the Python code. Do NOT wrap with markdown backticks.\n\n"
            f"INSTRUCTION:\n{instruction}\n\n"
            f"ARCHITECT ANALYSIS:\n{analysis_text}\n\n"
            f"CODE:\n{code}\n"
        )

        refactor_msg = await asyncio.to_thread(
            llm.invoke,
            [SystemMessage(content="You are a Senior Python Developer. Return only valid Python code."),
             HumanMessage(content=refactor_prompt)],
        )
        final_code = _content(refactor_msg).strip()

        job["result"] = final_code
        job["status"] = "completed"
        job["logs"].append("✅ Refactor completed successfully.")

    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        job["logs"].append(f"❌ [System] Error: {str(e)}")

@mcp.ingest(
    is_long_running=True,
)
async def start_refactoring_job(code_snippet: str, instruction: str) -> str:
    """
    Submits a Python code snippet to the Engineering Crew for refactoring.
    Returns a Job ID immediately (does not wait for completion).
    """
    job_id = f"JOB-{str(uuid.uuid4())[:8]}"
    JOBS[job_id] = {
        "status": "running",
        "logs": ["Job submitted to Bedrock..."],
        "result": None
    }
    asyncio.create_task(_run_refactoring_crew(job_id, code_snippet, instruction))
    return f"JOB_ID:{job_id}"

@mcp.ingest()
async def get_job_status(job_id: str) -> str:
    """
    Checks the status of a refactoring job. Returns logs or the final result.
    """
    clean_id = job_id.replace("JOB_ID:", "").strip()
    job = JOBS.get(clean_id)
    if not job: return "Error: Job ID not found."

    if job["status"] == "running":
        return f"STATUS: RUNNING\nLOGS:\n{job['logs'][-1]}"
    elif job["status"] == "completed":
        return f"STATUS: COMPLETED\nRESULT:\n{job['result']}"
    else:
        return f"STATUS: FAILED\nERROR: {job.get('error')}"

if __name__ == "__main__":
    mcp.run(transport="sse")
