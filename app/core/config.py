# app/core/config.py
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
        description="Ambiente de execução (development, production)."
    )
    port: int = Field(
        default=8000,
        description="Porta de execução do servidor FastAPI."
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