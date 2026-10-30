# app/infrastructure/nodes/specialist_node.py
from datetime import datetime
from typing import List, Literal, Optional, Tuple
from zoneinfo import ZoneInfo

from app.core.logger import logger
from app.domain.interfaces import AppTool, IGraphNode, ILLMAgent
from app.domain.models import GraphState

# Dicionário simples para garantir o dia da semana em português sem depender de locale do SO
DIAS_SEMANA = [
    "Segunda-feira", "Terça-feira", "Quarta-feira", 
    "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"
]

class SpecialistAgentNode(IGraphNode):
    """
    Nó Especialista (Tipo: LLM).
    Contabiliza caracteres de instrução, entrada do usuário e resposta final para estimativa de tokens.
    Agora possui consciência temporal injetada dinamicamente.
    """
    def __init__(
        self,
        node_name: str,
        llm_agent: ILLMAgent,
        system_instruction: str,
        next_node: str = "END_SUCCESS",
        tools: Optional[List[AppTool]] = None,
        knowledge_store_id: Optional[str] = None,
        timezone: str = "America/Sao_Paulo",
    ) -> None:
        self.node_name = node_name
        self.llm_agent = llm_agent
        self.system_instruction = system_instruction
        self.next_node = next_node
        self.tools = tools or []
        self.knowledge_store_id = knowledge_store_id
        self.timezone = timezone

    @property
    def node_type(self) -> Literal["LLM", "DETERMINISTIC"]:
        return "LLM"
        
    def _get_temporal_context(self) -> str:
        """Gera o cabeçalho de tempo atualizado no fuso correto."""
        agora = datetime.now(ZoneInfo(self.timezone))
        dia_semana_str = DIAS_SEMANA[agora.weekday()]
        data_hora_str = agora.strftime(f"%d/%m/%Y %H:%M")
        return f"[CONTEXTO DE TEMPO DO SISTEMA: Hoje é {dia_semana_str}, {data_hora_str}]\n\n"

    async def execute(self, state: GraphState) -> Tuple[GraphState, str]:
        effective_input = state.current_input
        
        # Lida com o loop de feedback do Juiz, se houver
        if state.errors_feedback:
            ultimo_erro = state.errors_feedback[-1]
            logger.warning(f"[FSM - {self.node_name}] Reexecutando com feedback do Juiz: {ultimo_erro}")
            effective_input = (
                f"Mensagem original do cliente: {state.current_input}\n\n"
                f"[ATENÇÃO - CORREÇÃO INTERNA OBRIGATÓRIA]: Sua tentativa de resposta anterior "
                f"('{state.final_response}') foi reprovada pelo auditor pelos seguintes motivos: "
                f"{ultimo_erro}. Corrija imediatamente sua ação/resposta seguindo estritamente as regras!"
            )

        # Injeta dinamicamente a data e hora na instrução de sistema do Tenant
        instrucao_com_tempo = self._get_temporal_context() + self.system_instruction

        agent_response = await self.llm_agent.generate_interaction(
            user_input=effective_input,
            system_instruction=instrucao_com_tempo,
            previous_interaction_id=state.interaction_id,
            tools=self.tools,
            knowledge_store_id=self.knowledge_store_id,
        )

        # Contabiliza entrada (system_instruction + input efetivo) e saída (resposta gerada)
        texto_entrada_completo = f"{instrucao_com_tempo}\n{effective_input}"
        state.record_node_usage(
            node_name=self.node_name,
            node_type=self.node_type,
            input_text=texto_entrada_completo,
            output_text=agent_response.text_output,
        )

        state.final_response = agent_response.text_output
        
        if agent_response.interaction_id:
            state.interaction_id = agent_response.interaction_id

        ferramentas_anteriores = state.internal_scratchpad.get("tools_called", [])
        state.internal_scratchpad["last_tools_called"] = agent_response.tools_called
        state.internal_scratchpad["tools_called"] = ferramentas_anteriores + agent_response.tools_called

        logger.info(
            f"[FSM - {self.node_name}] Resposta gerada. Tools acionadas: {agent_response.tools_called}. "
            f"Próximo nó: '{self.next_node}'."
        )

        return state, self.next_node