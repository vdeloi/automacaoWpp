# app/services/base_agent.py
from abc import ABC, abstractmethod
from typing import Any, List, Optional
from pydantic import BaseModel, Field


class AgentResponse(BaseModel):
    """Modelo de dados padronizado para representar a resposta de qualquer LLM."""
    text_output: str = Field(
        description="Texto final gerado pelo modelo para ser enviado ao utilizador."
    )
    interaction_id: Optional[str] = Field(
        default=None,
        description="Identificador único do turno/conversa mantido pelo provedor de IA."
    )
    tools_called: List[str] = Field(
        default_factory=list,
        description="Lista de nomes das funções/ferramentas executadas nesta interação."
    )
    raw_response: Optional[Any] = Field(
        default=None,
        description="Resposta bruta original retornada pela API (útil para debug)."
    )


class BaseLLMAgent(ABC):
    """Classe Abstrata de Agente Stateless (segura para concorrência)."""

    def __init__(self, model_name: str, default_system_instruction: Optional[str] = None):
        self.model_name = model_name
        self.default_system_instruction = default_system_instruction

    @abstractmethod
    async def generate_interaction(
        self,
        user_input: str,
        system_instruction: Optional[str] = None,
        previous_interaction_id: Optional[str] = None,
        **kwargs: Any
    ) -> AgentResponse:
        """Método abstrato assíncrono para gerar uma interação multiturno."""
        pass