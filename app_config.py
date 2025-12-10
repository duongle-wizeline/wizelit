from typing import List

class AppConfig:
    """
    Central configuration for the application.
    """

    # Model configurations by provider
    MODELS = {
        "openai": [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
        ],
        "anthropic": [
            "claude-sonnet-4-5-20250929",
            "claude-sonnet-4-20250514",
            "claude-opus-4-20250514",
            "claude-haiku-4-5-20251001",
        ],
        "ollama": [
            "llama3.3",
            "llama3.2",
            "mistral",
            "mixtral",
            "phi4",
            "qwen2.5",
        ],
        "groq": [
            "llama-3.3-70b-versatile",
            "llama-3.1-70b-versatile",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ],
    }

    def get_available_models(self, provider: str) -> List[str]:
        """
        Get available models for a provider.

        Args:
            provider: Provider name

        Returns:
            List of model names
        """
        return self.MODELS.get(provider.lower(), [])


config = AppConfig()
