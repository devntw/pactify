from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Cloud OpenAI (leave base_url empty)
    openai_api_key: str = ""
    # LM Studio / Ollama / any OpenAI-compatible server, e.g. http://localhost:1234/v1
    openai_base_url: Optional[str] = None
    # Model id: gpt-4o-mini (OpenAI) or the id shown in LM Studio for the loaded model
    openai_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 120.0
    # Some local servers do not support response_format json_object; set false for those
    llm_json_response_format: bool = True
    # Optional downstream endpoint for forwarding Layer 2 output
    layer3_url: str = ""
    layer3_timeout_seconds: float = 10.0


settings = Settings()
