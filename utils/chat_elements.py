import chainlit as cl
from app_config import config

def create_chat_settings() -> cl.ChatSettings:
    return cl.ChatSettings(
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
    )
