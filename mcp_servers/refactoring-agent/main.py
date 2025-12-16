import asyncio
import uuid
import os
import time
import contextlib
from typing import Dict, Any
from utils.bedrock_config import normalize_aws_env, resolve_bedrock_model_id

# FastMCP
from core.wizelit_agent_wrapper import WizelitAgentWrapper

# CrewAI
from crewai import Agent, Task, Crew
from crewai.llm import LLM
from crewai.process import Process

# Initialize FastMCP
mcp = WizelitAgentWrapper("RefactoringCrewAgent", port=1337)

# In-Memory Job Store
JOBS: Dict[str, Dict[str, Any]] = {}


def _append_log(job: Dict[str, Any], message: str) -> None:
    """Append a timestamped log line to a job record."""
    ts = time.strftime("%H:%M:%S")
    job.setdefault("logs", []).append(f"[{ts}] {message}")


async def _heartbeat(job_id: str, interval_seconds: float = 5.0) -> None:
    """
    Periodically append a heartbeat log while a job is running so the UI
    has visible progress even during long LLM calls.
    """
    start = time.monotonic()
    while True:
        await asyncio.sleep(interval_seconds)
        job = JOBS.get(job_id)
        if not job or job.get("status") != "running":
            return
        elapsed = int(time.monotonic() - start)
        _append_log(job, f"⏳ Still working... ({elapsed}s)")


async def _run_refactoring_crew(job_id: str, code: str, instruction: str):
    """
    Refactor code using CrewAI in two steps:
    1) Architect-style analysis + plan
    2) Code-only refactor output

    NOTE: We explicitly configure a Bedrock-backed model for CrewAI so it
    doesn't fall back to OpenAI (and doesn't require OPENAI_API_KEY).
    """
    try:
        job = JOBS[job_id]

        # 1) Configure CrewAI LLM (Bedrock via LiteLLM model string).
        #
        # Default is derived from CHAT_MODEL_ID to keep configuration familiar.
        # Example default model string:
        #   bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
        _append_log(job, "🧠 Starting CrewAI refactoring crew...")
        _append_log(job, "🔧 Resolving Bedrock configuration...")
        region = normalize_aws_env(default_region="ap-southeast-2")
        model_id = resolve_bedrock_model_id()
        default_crewai_model = f"bedrock/{model_id}"
        crewai_model = os.getenv("CREWAI_MODEL", default_crewai_model)
        _append_log(job, f"🌎 Bedrock region: {region}")
        _append_log(job, f"🤖 CrewAI model: {crewai_model}")

        # Help Bedrock provider resolution (different libs read different env vars).
        # (Already normalized above; keep for backward compatibility.)
        os.environ.setdefault("AWS_REGION", region)
        os.environ.setdefault("AWS_REGION_NAME", region)

        llm = LLM(
            model=crewai_model,
            temperature=0,
            timeout=float(os.getenv("CREWAI_TIMEOUT_SECONDS", "120")),
        )

        _append_log(job, "🧩 Creating agents...")

        architect = Agent(
            role="Senior Software Architect",
            goal="Analyze the code and propose a concise refactoring plan aligned with SOLID and clean architecture.",
            backstory="You are pragmatic and prioritize correctness, testability, and clear boundaries.",
            llm=llm,
            verbose=False,
            allow_delegation=False,
        )

        developer = Agent(
            role="Senior Python Developer",
            goal="Refactor the code according to the instruction and the architect plan, returning only valid Python code.",
            backstory="You write clean, typed Python and keep behavior changes minimal unless required by the instruction.",
            llm=llm,
            verbose=False,
            allow_delegation=False,
        )

        _append_log(job, "🧪 Preparing tasks...")
        analysis_task = Task(
            description=(
                "Analyze the following Python code according to the user's instruction.\n"
                "Identify the top 3 critical issues (e.g., global state, lack of typing, tight coupling, poor naming).\n"
                "Then propose a short refactoring plan.\n\n"
                f"INSTRUCTION:\n{instruction}\n\n"
                f"CODE:\n{code}\n"
            ),
            expected_output="A bullet list of the top 3 issues and a short refactoring plan.",
            agent=architect,
        )

        refactor_task = Task(
            description=(
                "Refactor the code based on the architect analysis and the instruction.\n"
                "Use Python type hints and (only when appropriate) Pydantic models.\n"
                "Output ONLY the Python code. Do NOT wrap with markdown backticks.\n\n"
                f"INSTRUCTION:\n{instruction}\n\n"
                f"CODE:\n{code}\n"
            ),
            expected_output="Refactored Python code only (no markdown, no explanations).",
            agent=developer,
            context=[analysis_task],
        )

        _append_log(job, "🧵 Building crew (sequential)...")
        crew = Crew(
            agents=[architect, developer],
            tasks=[analysis_task, refactor_task],
            process=Process.sequential,
            verbose=False,
        )

        # CrewAI kickoff is synchronous; run it off the event loop thread.
        _append_log(job, "🚀 Kickoff started (analysis → refactor)...")
        heartbeat_task = asyncio.create_task(_heartbeat(job_id))
        try:
            # Capture any stdout/stderr from CrewAI internals (even if verbose=False).
            # This avoids noisy terminal spam while still surfacing errors/notes in logs.
            def _kickoff_captured():
                import io
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    out = crew.kickoff()
                return out, buf.getvalue()

            crew_output, kickoff_io = await asyncio.to_thread(_kickoff_captured)
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

        # Prefer the final task output, but fall back gracefully.
        _append_log(job, "📦 Kickoff finished, extracting final code...")
        if kickoff_io and kickoff_io.strip():
            # Keep this bounded so we don't blow up the UI.
            tail = kickoff_io.strip().splitlines()[-50:]
            _append_log(job, "🪵 Crew output (tail):")
            for line in tail:
                _append_log(job, line)

        final_code = None
        try:
            tasks_output = getattr(crew_output, "tasks_output", None) or []
            if tasks_output:
                final_code = getattr(tasks_output[-1], "raw", None)
        except Exception:
            final_code = None
        final_code = (final_code or getattr(crew_output, "raw", "") or "").strip()

        job["result"] = final_code
        job["status"] = "completed"
        _append_log(job, "✅ Refactor completed successfully.")

    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        _append_log(job, f"❌ [System] Error: {str(e)}")

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
        "logs": [],
        "result": None
    }
    _append_log(JOBS[job_id], "📨 Job submitted.")
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
        tail_n = int(os.getenv("JOB_LOG_TAIL", "25"))
        logs = job.get("logs", [])
        tail = "\n".join(logs[-tail_n:]) if logs else "[no logs yet]"
        return f"STATUS: RUNNING\nLOGS:\n{tail}"
    elif job["status"] == "completed":
        tail_n = int(os.getenv("JOB_LOG_TAIL", "25"))
        logs = job.get("logs", [])
        tail = "\n".join(logs[-tail_n:]) if logs else "[no logs]"
        # Include logs even on completion so callers don't miss the final wrap-up lines.
        return f"STATUS: COMPLETED\nLOGS:\n{tail}\nRESULT:\n{job['result']}"
    else:
        tail_n = int(os.getenv("JOB_LOG_TAIL", "25"))
        logs = job.get("logs", [])
        tail = "\n".join(logs[-tail_n:]) if logs else "[no logs]"
        return f"STATUS: FAILED\nLOGS:\n{tail}\nERROR: {job.get('error')}"

if __name__ == "__main__":
    mcp.run(transport="sse")