# app/tenants/padaria/tenant.py
from typing import Dict, List, Tuple
from app.domain.interfaces import AppTool, IGraphNode, ILLMAgent
from app.infrastructure.nodes import SpecialistAgentNode, TurnPredictorNode
from app.services.message_buffer import HumanMessageBuffer
from app.tenants.base import BaseTenant
from app.tenants.padaria.prompts import SYSTEM_INSTRUCTION
from app.tenants.padaria.tools import PadariaDeliveryToolService


class PadariaTenant(BaseTenant):
    """
    Tenant da Padaria Pão Quente.
    Utiliza um grafo linear simples de 2 nós: Preditor de Turno -> Agente de Balcão.
    """

    def __init__(
        self,
        tool_service: PadariaDeliveryToolService,
        llm_agent: ILLMAgent,
        message_buffer: HumanMessageBuffer,
    ) -> None:
        super().__init__()
        self._tool_service = tool_service
        self._llm_agent = llm_agent
        self._message_buffer = message_buffer

    @property
    def tenant_id(self) -> str:
        return "padaria_01"

    @property
    def name(self) -> str:
        return "Padaria Pão Quente"

    @property
    def system_instruction(self) -> str:
        return SYSTEM_INSTRUCTION

    def get_tools(self) -> List[AppTool]:
        return self._tool_service.get_tools()

    def build_graph(self) -> Tuple[Dict[str, IGraphNode], str]:
        nodes: Dict[str, IGraphNode] = {
            "preditor_turno": TurnPredictorNode(
                message_buffer=self._message_buffer,
                next_node="agente_balcao",
            ),
            "agente_balcao": SpecialistAgentNode(
                node_name="PadariaBalcaoNode",
                llm_agent=self._llm_agent,
                system_instruction=self.system_instruction,
                tools=self.get_tools(),
                knowledge_store_id=self.file_search_store_id,
                next_node="END_SUCCESS",
            ),
        }
        return nodes, "preditor_turno"