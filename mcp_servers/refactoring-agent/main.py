import asyncio
import os
import time
import contextlib
from utils.bedrock_config import normalize_aws_env, resolve_bedrock_model_id

# FastMCP
from core.wizelit_agent_wrapper import WizelitAgentWrapper, Job

# CrewAI
from crewai import Agent, Task, Crew
from crewai.llm import LLM
from crewai.process import Process

# Initialize FastMCP
mcp = WizelitAgentWrapper("RefactoringCrewAgent", port=1337)


async def _run_refactoring_crew(job: Job, code: str, instruction: str):
    """
    Refactor code using CrewAI in two steps:
    1) Architect-style analysis + plan
    2) Code-only refactor output

    NOTE: We explicitly configure a Bedrock-backed model for CrewAI so it
    doesn't fall back to OpenAI (and doesn't require OPENAI_API_KEY).
    """
    try:
        # 1) Configure CrewAI LLM (Bedrock via LiteLLM model string).
        #
        # Default is derived from CHAT_MODEL_ID to keep configuration familiar.
        # Example default model string:
        #   bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
        job.logger.info("🧠 Starting CrewAI refactoring crew...")
        job.logger.info("🔧 Resolving Bedrock configuration...")
        region = normalize_aws_env(default_region="ap-southeast-2")
        model_id = resolve_bedrock_model_id()
        default_crewai_model = f"bedrock/{model_id}"
        crewai_model = os.getenv("CREWAI_MODEL", default_crewai_model)
        job.logger.info(f"🌎 Bedrock region: {region}")
        job.logger.info(f"🤖 CrewAI model: {crewai_model}")

        # Help Bedrock provider resolution (different libs read different env vars).
        # (Already normalized above; keep for backward compatibility.)
        os.environ.setdefault("AWS_REGION", region)
        os.environ.setdefault("AWS_REGION_NAME", region)

        llm = LLM(
            model=crewai_model,
            temperature=0,
            timeout=float(os.getenv("CREWAI_TIMEOUT_SECONDS", "120")),
        )

        job.logger.info("🧩 Creating agents...")

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

        job.logger.info("🧪 Preparing tasks...")
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

        job.logger.info("🧵 Building crew (sequential)...")
        crew = Crew(
            agents=[architect, developer],
            tasks=[analysis_task, refactor_task],
            process=Process.sequential,
            verbose=False,
        )

        # CrewAI kickoff is synchronous; run it off the event loop thread.
        job.logger.info("🚀 Kickoff started (analysis → refactor)...")
        # Capture any stdout/stderr from CrewAI internals (even if verbose=False).
        # This avoids noisy terminal spam while still surfacing errors/notes in logs.
        def _kickoff_captured():
            import io
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                out = crew.kickoff()
            return out, buf.getvalue()

        crew_output, kickoff_io = await asyncio.to_thread(_kickoff_captured)

        # Prefer the final task output, but fall back gracefully.
        job.logger.info("📦 Kickoff finished, extracting final code...")
        if kickoff_io and kickoff_io.strip():
            # Keep this bounded so we don't blow up the UI.
            tail = kickoff_io.strip().splitlines()[-50:]
            job.logger.info("🪵 Crew output (tail):")
            for line in tail:
                job.logger.info(line)

        final_code = None
        try:
            tasks_output = getattr(crew_output, "tasks_output", None) or []
            if tasks_output:
                final_code = getattr(tasks_output[-1], "raw", None)
        except Exception:
            final_code = None
        final_code = (final_code or getattr(crew_output, "raw", "") or "").strip()
        job.logger.info("✅ Refactor completed successfully.")

        return final_code

    except Exception as e:
        job.logger.error(f"❌ [System] Error: {str(e)}")
        raise

@mcp.ingest(
    is_long_running=True,
)
async def start_refactoring_job(code_snippet: str, instruction: str, job: Job) -> str:
    """
    Submits a Python code snippet to the Engineering Crew for refactoring.
    Returns a Job ID immediately (does not wait for completion).
    """
    job.logger.info("📨 Job submitted.")
    # Run the refactoring crew in the background while Job manages status, result, and heartbeat
    job.run(_run_refactoring_crew(job, code_snippet, instruction))
    return job.id

@mcp.ingest()
async def get_job_status(job_id: str) -> str:
    """
    Checks the status of a refactoring job. Returns logs or the final result.
    """
    job = mcp.get_job(job_id)
    if not job:
        return "Error: Job ID not found."

    tail_n = int(os.getenv("JOB_LOG_TAIL", "25"))
    logs = mcp.get_job_logs(job_id) or []
    tail = "\n".join(logs[-tail_n:]) if logs else "[no logs yet]"

    if job.status == "running":
        return f"STATUS: RUNNING\nLOGS:\n{tail}"
    elif job.status == "completed":
        # Include logs even on completion so callers don't miss the final wrap-up lines.
        return f"STATUS: COMPLETED\nLOGS:\n{tail}\nRESULT:\n{job.result or ''}"
    else:
        return f"STATUS: FAILED\nLOGS:\n{tail}\nERROR: {job.error or 'Unknown error'}"

if __name__ == "__main__":
    mcp.run(transport="sse")
    # mcp.run()
