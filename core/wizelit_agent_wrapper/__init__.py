try:
    # Prefer the extracted implementation in wizelit-sdk when available
    from wizelit_sdk.agent_wrapper.agent_wrapper import (
        WizelitAgentWrapper,
        LLM_FRAMEWORK_CREWAI,
        LLM_FRAMEWORK_LANGCHAIN,
        LLM_FRAMEWORK_LANGGRAPH,
        LlmFrameworkType,
        CurrentJob,
    )
    from wizelit_sdk.agent_wrapper.job import Job
except Exception:
    # Fallback to local implementation if SDK is not installed
    from .wizelit_agent_wrapper import (
        WizelitAgentWrapper,
        LLM_FRAMEWORK_CREWAI,
        LLM_FRAMEWORK_LANGCHAIN,
        LLM_FRAMEWORK_LANGGRAPH,
        LlmFrameworkType,
        CurrentJob,
    )
    from .job import Job
