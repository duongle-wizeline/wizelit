from langchain_core.tools import tool
from open_search_client import get_opensearch_client

@tool("deep_research", description="Deep research information related to a query.")
def deep_research(query: str):
    """Deep research information related to a query."""
    openSearchClient = get_opensearch_client()

    print(f'Query: {query}')
    retrieved_docs = openSearchClient.search(
        index="rag_practice_2",
        body={
            "query": {
                "match": {
                    "content": query
                },
            }
        }
    )

    # Extract hits from OpenSearch response
    hits = retrieved_docs.get("hits", {}).get("hits", [])
    print(f"Retrieved {len(hits)} documents from OpenSearch.")

    # Serialize the results
    serialized = "\n\n".join(
        (f"Source: {hit['_source']['metadata']}\nContent: {hit['_source']['content']}")
        for hit in hits
    )
    return serialized

@tool("quick_search", description="Quick search summary of LLM Powered Autonomous Agents blog post.")
def quick_search(query: str):
    """Quick search summary of LLM Powered Autonomous Agents blog post."""

    print(f'Quick search: {query}')
    return "Lilian Weng's blog post 'LLM Powered Autonomous Agents' provides a comprehensive overview of building agents where a Large Language Model (LLM) acts as the primary controller or 'brain'. The system is designed around three essential components that augment the LLM's capabilities: Planning, Memory, and Tool Use.\n\nFirst, the Planning component allows the agent to handle complex tasks by breaking them down into smaller, manageable subgoals through 'Task Decomposition'. Techniques such as Chain of Thought (CoT) and Tree of Thoughts (ToT) enable the model to think step-by-step and explore multiple reasoning possibilities. Furthermore, the agent utilizes 'Self-Reflection' to improve iteratively. Frameworks like ReAct combine reasoning and acting to interact with the environment, while Reflexion uses a heuristic to detect hallucinations or inefficient planning, allowing the agent to reset and learn from past mistakes. Other methods like Chain of Hindsight (CoH) and Algorithm Distillation (AD) help the model refine its outputs based on feedback or learning histories.\n\nSecond, the Memory component is crucial for retaining information. The post draws parallels between human memory and agent architecture: Sensory Memory is represented by raw input embeddings; Short-Term Memory is handled by in-context learning, which is limited by the Transformer's context window; and Long-Term Memory is achieved through external vector stores. To efficiently recall information from this long-term storage, agents use Maximum Inner Product Search (MIPS) optimized by Approximate Nearest Neighbors (ANN) algorithms like LSH, ANNOY, or HNSW. This allows the agent to retrieve relevant information based on relevance, recency, and importance.\n\nThird, Tool Use extends the agent's capabilities beyond its pre-trained weights by granting access to external APIs. This enables the agent to access up-to-date information, execute code, or utilize proprietary data. Benchmarks like API-Bank measure an agent's ability to search for, retrieve, and plan using these tools. The article highlights HuggingGPT as an example where ChatGPT acts as a controller to manage various expert models.\n\nFinally, the post examines several Case Studies. 'ChemCrow' demonstrates a domain-specific agent using expert tools for organic synthesis and drug discovery. 'Generative Agents' creates a Sims-like sandbox where 25 virtual characters interact socially, utilizing a complex architecture of memory streams and reflection to produce believable behavior. Proof-of-concepts like AutoGPT and GPT-Engineer show the potential of autonomous agents to pursue user-defined goals, though they currently face challenges regarding reliability and infinite loops."
