import asyncio
import uuid
from typing import Dict, Any, List
from mcp.server.fastmcp import FastMCP

# --- CHANGE HERE: Configure port in the constructor ---
# We also remove 'dependencies' as asyncio is built-in and often doesn't need declaring
mcp = FastMCP("RefactoringCrewAgent", port=1337)

# In-Memory Job Store
JOBS: Dict[str, Dict[str, Any]] = {}

async def _run_refactoring_crew(job_id: str, code: str, instruction: str):
    """
    Simulates the 'Slow' work. In production, this would call CrewAI.kickoff().
    """
    try:
        job = JOBS[job_id]
        
        # Step 1: Analysis
        await asyncio.sleep(2) 
        job["logs"].append(f"[Architect] Analyzing code structure for: {instruction}...")
        
        await asyncio.sleep(2)
        job["logs"].append(f"[Architect] Found 'God Object' anti-pattern in the snippet.")
        
        # Step 2: Planning
        await asyncio.sleep(2)
        job["logs"].append(f"[TechLead] Drafting refactoring plan: Extracting 'PaymentLogic' class.")
        
        # Step 3: Coding
        await asyncio.sleep(3)
        job["logs"].append(f"[Coder] Writing Pydantic models...")
        
        # Step 4: Completion
        final_code = f"""
# REFACTORED CODE (Based on: {instruction})
from pydantic import BaseModel

class RefactoredModel(BaseModel):
    # Logic extracted from your legacy code
    id: str
    amount: float

def process_payment(data: RefactoredModel):
    '''Processed safely.'''
    return True
"""
        job["result"] = final_code
        job["status"] = "completed"
        job["logs"].append("[System] Job Finished.")
        
    except Exception as e:
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = str(e)

# --- TOOL 1: The Trigger ---

@mcp.tool()
async def start_refactoring_job(code_snippet: str, instruction: str) -> str:
    """
    Submits a Python code snippet to the Engineering Crew for refactoring.
    Returns a Job ID immediately (does not wait for completion).
    """
    job_id = f"JOB-{str(uuid.uuid4())[:8]}"
    
    # Initialize Job State
    JOBS[job_id] = {
        "status": "running",
        "logs": ["Job submitted. Waiting for crew..."],
        "result": None
    }
    
    # Start the background task (Fire and Forget)
    asyncio.create_task(_run_refactoring_crew(job_id, code_snippet, instruction))
    
    return f"JOB_ID:{job_id}"

# --- TOOL 2: The Poller ---

@mcp.tool()
async def get_job_status(job_id: str) -> str:
    """
    Checks the status of a refactoring job. Returns logs or the final result.
    """
    clean_id = job_id.replace("JOB_ID:", "").strip()
    
    job = JOBS.get(clean_id)
    if not job:
        return "Error: Job ID not found."

    if job["status"] == "running":
        # Return the last few logs to simulate streaming
        recent_logs = "\n".join(job["logs"][-3:])
        return f"STATUS: RUNNING\nLOGS:\n{recent_logs}"
    
    elif job["status"] == "completed":
        return f"STATUS: COMPLETED\nRESULT:\n{job['result']}"
    
    else:
        return f"STATUS: FAILED\nERROR: {job.get('error')}"

if __name__ == "__main__":
    # "http" is invalid. Use "streamable-http" (standard) or "sse"
    mcp.run(transport="streamable-http")