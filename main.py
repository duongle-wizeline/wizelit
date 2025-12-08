import chainlit as cl
from typing import List, Dict

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

@cl.on_chat_start
async def on_chat_start():
    """
    Called when a new chat session starts.
    Initialize user's AI agent based on their preferences.
    """
    # Get user settings or use defaults
    settings = await cl.ChatSettings(
        [
            cl.input_widget.Select(
                id="model_provider",
                label="AI Model Provider",
                values=["openai", "anthropic", "ollama", "groq"],
                initial_value="openai",
            ),
            cl.input_widget.Select(
                id="model_name",
                label="Model Name",
                values=config.get_available_models("openai"),
                initial_value="gpt-4o-mini",
            ),
            cl.input_widget.Slider(
                id="temperature",
                label="Temperature",
                initial=0.7,
                min=0,
                max=2,
                step=0.1,
            ),
            cl.input_widget.Slider(
                id="max_tokens",
                label="Max Tokens",
                initial=2048,
                min=256,
                max=8192,
                step=256,
            ),
        ]
    ).send()

@cl.on_message
async def main(message: cl.Message):
    # Your custom logic goes here...

    # Send a response back to the user
    await cl.Message(
        content=f"Received: {message.content}",
    ).send()
