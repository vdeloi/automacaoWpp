# app/tenants/clinica/tenant.py
from typing import Dict, List, Tuple
from google import genai

from app.domain.interfaces import AppTool, IGraphNode, ILLMAgent
from app.infrastructure.nodes import (
    JudgeEvaluatorNode,
    RouterNode,
    SpecialistAgentNode,
    TurnPredictorNode,
)
from app.services.message_buffer import HumanMessageBuffer
from app.tenants.base import BaseTenant
from app.tenants.clinica.prompts import (
    PROMPT_AGENTE_AGENDAMENTO,
    PROMPT_AGENTE_DUVIDAS,
    REGRAS_JUIZ_CLINICA,
    SYSTEM_INSTRUCTION,
)
from app.tenants.clinica.tools import ClinicaToolService


class ClinicaTenant(BaseTenant):
    """
    Tenant da Clínica Odontológica Sorriso.
    Implementa um Grafo Direcionado completo com Roteador, Agente RAG,
    Agente Transacional de Agendamento e Juiz Avaliador com Circuit Breaker.
    """

    def __init__(
        self,
        tool_service: ClinicaToolService,
        llm_agent: ILLMAgent,
        message_buffer: HumanMessageBuffer,
        genai_client: genai.Client,
        classifier_model_name: str,
    ) -> None:
        super().__init__()
        self._tool_service = tool_service
        self._llm_agent = llm_agent
        self._message_buffer = message_buffer
        self._genai_client = genai_client
        self._classifier_model_name = classifier_model_name

    @property
    def tenant_id(self) -> str:
        return "clinica_01"

    @property
    def name(self) -> str:
        return "Clínica Odontológica Sorriso"

    @property
    def system_instruction(self) -> str:
        return SYSTEM_INSTRUCTION

    def get_tools(self) -> List[AppTool]:
        return self._tool_service.get_tools()

    def build_graph(self) -> Tuple[Dict[str, IGraphNode], str]:
        rotas_clinica = {
            "agente_agendamento": (
                "Quando o paciente quiser marcar/agendar consulta, verificar horários livres, "
                "informar data, horário, nome do médico/dentista ou seu próprio nome para agendamento."
            ),
            "agente_duvidas": (
                "Quando o paciente tiver dúvidas gerais sobre a clínica, endereço, convênios, "
                "valores, procedimentos ou apenas enviar um cumprimento inicial sem pedir agendamento."
            ),
        }

        nodes: Dict[str, IGraphNode] = {
            "preditor_turno": TurnPredictorNode(
                message_buffer=self._message_buffer,
                next_node="roteador",
            ),
            "roteador": RouterNode(
                client=self._genai_client,
                routes_description=rotas_clinica,
                default_route="agente_agendamento",
                model_name=self._classifier_model_name,
            ),
            "agente_duvidas": SpecialistAgentNode(
                node_name="ClinicaRAGNode",
                llm_agent=self._llm_agent,
                system_instruction=PROMPT_AGENTE_DUVIDAS,
                tools=[],
                knowledge_store_id=self.file_search_store_id,
                next_node="END_SUCCESS",
            ),
            "agente_agendamento": SpecialistAgentNode(
                node_name="ClinicaBookingNode",
                llm_agent=self._llm_agent,
                system_instruction=PROMPT_AGENTE_AGENDAMENTO,
                tools=self.get_tools(),
                knowledge_store_id=self.file_search_store_id,
                next_node="juiz_avaliador",
            ),
            "juiz_avaliador": JudgeEvaluatorNode(
                client=self._genai_client,
                evaluation_rules=REGRAS_JUIZ_CLINICA,
                retry_node="agente_agendamento",
                model_name=self._classifier_model_name,
                success_node="END_SUCCESS",
                handoff_node="END_HANDOFF",
                max_retries=2,
            ),
        }

        return nodes, "preditor_turno"