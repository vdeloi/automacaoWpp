# app/models/chat.py
from typing import Literal, Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """
    Estrutura da mensagem enviada pelo cliente através do WhatsApp Mock.
    Inclui o identificador do estabelecimento comercial (tenant_id).
    """
    tenant_id: str = Field(
        ...,
        description="Identificador único da empresa (ex: 'clinica_01' ou 'padaria_01')."
    )
    phone_number: str = Field(
        ...,
        description="Número de telefone do cliente com DDD (ex: 5511999999999)."
    )
    message: str = Field(
        ...,
        min_length=1,
        description="Texto da mensagem digitada pelo cliente."
    )


class ChatResponse(BaseModel):
    """
    Estrutura da resposta retornada para o WhatsApp Mock.
    """
    response: str = Field(
        ...,
        description="Texto da resposta gerada pelo assistente."
    )
    interaction_id: Optional[str] = Field(
        default=None,
        description="Identificador da sessão no servidor do provedor de IA (ex: Google)."
    )
    status: Literal["success", "buffered", "error"] = Field(
        default="success",
        description="Status do processamento ('success', 'buffered' ou 'error')."
    )