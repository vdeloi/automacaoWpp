# app/domain/interfaces.py
import asyncio
import inspect
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple
from pydantic import BaseModel, Field

from app.domain.models import GraphState


# --- DTOs de Domínio ---
class AgentResponse(BaseModel):
    text_output: str
    interaction_id: Optional[str] = None
    tools_called: List[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    raw_response: Optional[Any] = None


class TurnPrediction(BaseModel):
    status: Literal["COMPLETE", "INCOMPLETE"]
    extra_wait_seconds: float
    reason: str


class IncomingMessage(BaseModel):
    tenant_id: str
    phone_number: str
    text: str


# --- Abstração de Ferramentas (Suporta Sync e Async Nativamente) ---
class AppTool:
    """Ferramenta agnóstica de LLM com suporte nativo a corrotinas (async def)."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable[..., Any],
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self._is_async = inspect.iscoroutinefunction(handler)

    @property
    def is_async(self) -> bool:
        return self._is_async

    def to_json_schema(self) -> Dict[str, Any]:
        """Formato neutro que qualquer adaptador de LLM (Gemini, OpenAI, Claude) pode consumir."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    async def execute_async(self, **kwargs: Any) -> Any:
        """Executa diretamente no Event Loop se for async, ou delega para thread se for síncrona."""
        if self._is_async:
            return await self.handler(**kwargs)
        return await asyncio.to_thread(self.handler, **kwargs)


# --- Contrato Polimórfico do Nó do Grafo (FSM) ---
class IGraphNode(ABC):
    """
    Interface base para qualquer nó da Máquina de Estados Finita (FSM).
    Exige a identificação do tipo do nó ('LLM' vs 'DETERMINISTIC') para auditoria de custos.
    """

    @property
    @abstractmethod
    def node_type(self) -> Literal["LLM", "DETERMINISTIC"]:
        """Define se o nó consome tokens de IA ('LLM') ou se roda código local ('DETERMINISTIC')."""
        pass

    @abstractmethod
    async def execute(self, state: GraphState) -> Tuple[GraphState, str]:
        """
        Processa o estado atual, modifica suas propriedades e retorna:
        (estado_atualizado, nome_do_proximo_no)
        """
        pass


# --- Interfaces (Ports) para Injeção de Dependência ---
class ISessionRepository(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass

    @abstractmethod
    async def save_message(self, session_id: str, tenant_id: str, role: str, content: str) -> None:
        pass

    @abstractmethod
    async def get_history(self, session_id: str, tenant_id: str, limit: int = 10) -> List[Dict[str, str]]:
        pass

    @abstractmethod
    async def get_last_interaction_id(self, session_id: str, tenant_id: str) -> Optional[str]:
        pass

    @abstractmethod
    async def save_interaction_id(self, session_id: str, tenant_id: str, interaction_id: str) -> None:
        pass

    @abstractmethod
    async def clear_session(self, session_id: str, tenant_id: str) -> None:
        pass


class IClinicaRepository(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass

    @abstractmethod
    async def check_availability(self, date: str, doctor: str) -> List[str]:
        pass

    @abstractmethod
    async def book_appointment(self, client_name: str, date: str, time: str, doctor: str) -> bool:
        pass


class ILLMAgent(ABC):
    @abstractmethod
    async def generate_interaction(
        self,
        user_input: str,
        system_instruction: str,
        previous_interaction_id: Optional[str] = None,
        tools: Optional[List[AppTool]] = None,
        knowledge_store_id: Optional[str] = None,
        max_turns: int = 5,
    ) -> AgentResponse:
        pass


class ITurnPredictor(ABC):
    @abstractmethod
    async def predict(self, accumulated_messages: List[str]) -> TurnPrediction:
        pass

    @abstractmethod
    def calculate_typing_delay(self, response_text: str) -> float:
        pass


class IMessagingGateway(ABC):
    """Permite trocar entre Mock Web, WhatsApp Cloud API, Evolution API ou Z-API."""

    @abstractmethod
    async def send_typing_indicator(self, tenant_id: str, phone_number: str, duration: float) -> None:
        pass

    @abstractmethod
    async def send_text_message(self, tenant_id: str, phone_number: str, text: str) -> None:
        pass


class IDashboardRepository(ABC):
    """Interface (Port) para persistência de telemetria e leitura de dados do Dashboard."""

    @abstractmethod
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass

    @abstractmethod
    async def save_execution_telemetry(self, state: GraphState, elapsed_seconds: float) -> None:
        pass

    @abstractmethod
    async def get_tenant_token_metrics(self, tenant_id: str, chars_per_token: float = 3.8) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_tenant_databases_snapshot(self, tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        pass