"""
config.py — QSR Signal Extraction Engine settings.
"""
from __future__ import annotations
import logging
from functools import lru_cache
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

class AppSettings(BaseSettings):
    # Azure OpenAI
    azure_openai_endpoint:    str  = Field(default="https://moodbit-gpt-useast2.openai.azure.com/")
    azure_openai_deployment:  str  = Field(default="gpt-5.5")
    azure_openai_api_version: str  = Field(default="2024-12-01-preview")
    azure_openai_api_key:     str  = Field(default="")
    # Serper.dev (OSINT Signal 3)
    serper_api_key:           str  = Field(default="")
    # Concurrency
    max_concurrent_leads:     int  = Field(default=2, ge=1, le=8)
    base_request_delay_seconds: float = Field(default=1.5, ge=0.0)
    max_jitter_seconds:       float = Field(default=2.0, ge=0.0)
    # Browser
    browser_headless:         bool = Field(default=True)
    browser_timeout_ms:       int  = Field(default=30000, ge=5000)
    # Paths
    leads_file_path:          Path = Field(default=Path("leads.txt"))
    output_dir:               Path = Field(default=Path("outputs"))
    csv_output_path:          Path = Field(default=Path("outputs/results.csv"))
    cache_db_path:            Path = Field(default=Path("outputs/cache/signals.db"))
    # Cache TTLs
    cache_ttl_store_locator_hours: int = Field(default=24)
    cache_ttl_ats_hours:          int = Field(default=12)
    cache_ttl_osint_hours:        int = Field(default=72)
    # Logging
    log_level: str = Field(default="INFO")

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG","INFO","WARNING","ERROR","CRITICAL"}
        upper = v.upper()
        if upper not in valid: raise ValueError(f"log_level must be one of {valid}")
        return upper

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "case_sensitive": False, "extra": "ignore"}

@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    s = AppSettings()
    s.output_dir.mkdir(parents=True, exist_ok=True)
    s.cache_db_path.parent.mkdir(parents=True, exist_ok=True)
    return s

def configure_stdlib_logging() -> None:
    Path("outputs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        filename="outputs/pipeline.log",
        filemode="a"
    )
