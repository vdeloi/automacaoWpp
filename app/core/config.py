# app/core/config.py
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configurações gerais da aplicação carregadas a partir de variáveis de ambiente (.env).
    """
    gemini_api_key: str = Field(
        default="",
        alias="GEMINI_API_KEY",
        description="Chave de autenticação da API do Google Gemini."
    )
    model_name: str = Field(
        default="gemini-3.6-flash",
        alias="MODEL_NAME",
        description="Modelo principal do Gemini utilizado nas interações da Interactions API."
    )
    # NOVO: Modelo leve e rápido dedicado exclusivamente a prever se o usuário terminou de digitar
    classifier_model_name: str = Field(
        default="gemini-3.5-flash-lite",
        alias="CLASSIFIER_MODEL_NAME",
        description="Modelo rápido e de baixo custo para previsão de fim de turno (Turn-Taking)."
    )
    environment: str = Field(
        default="development",
        alias="ENVIRONMENT",
        description="Ambiente de execução (development, production)."
    )
    port: int = Field(
        default=8000,
        alias="PORT",
        description="Porta de execução do servidor FastAPI."
    )
    enable_debug_logs: bool = Field(
        default=False,
        alias="ENABLE_DEBUG_LOGS",
        description="Se True, gera arquivos de log detalhados com métricas e avaliação de alucinação."
    )

    # --- Configurações de Persistência e Mensageria (Injeção de Dependência) ---
    sessions_db_path: str = Field(
        default="data/app_sessions.db",
        alias="SESSIONS_DB_PATH",
        description="Caminho do arquivo SQLite para armazenamento de histórico e sessões de chat."
    )
    clinica_db_path: str = Field(
        default="data/tenants/clinica_01/clinica.db",
        alias="CLINICA_DB_PATH",
        description="Caminho do arquivo SQLite exclusivo para agendamentos da clínica."
    )
    whatsapp_api_url: Optional[str] = Field(
        default=None,
        alias="WHATSAPP_API_URL",
        description="URL base da API de WhatsApp (ex: Evolution API). Se vazio, utiliza o Mock Gateway."
    )
    whatsapp_api_key: Optional[str] = Field(
        default=None,
        alias="WHATSAPP_API_KEY",
        description="Chave de autenticação da API de WhatsApp externa."
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


def get_settings() -> Settings:
    """Função de fábrica para instanciar as configurações com tratamento defensivo."""
    try:
        settings_instance = Settings()
    except Exception as error:
        print(f"[ERRO CRÍTICO] Falha ao carregar as variáveis de ambiente: {error}")
        raise error
    else:
        return settings_instance


settings = get_settings()