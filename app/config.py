from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "sqlite:///./tri9t.db"
    generated_store_path: str = "./data/generated.json"
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_api_key: str = ""
    llm_model: str = "llama-3.3-70b-versatile"
    llm_timeout_seconds: int = 45
    max_llm_retries: int = 2
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
