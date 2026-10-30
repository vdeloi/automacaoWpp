# app/infrastructure/nodes/turn_predictor_node.py
from typing import Literal, Tuple
from app.core.logger import logger
from app.domain.interfaces import IGraphNode
from app.domain.models import GraphState
from app.services.message_buffer import HumanMessageBuffer


class TurnPredictorNode(IGraphNode):
    """
    Nó de entrada que agrupa mensagens picadas no buffer.
    Identifica dinamicamente se usou regra rápida em Python (DETERMINISTIC) ou IA (LLM).
    """

    def __init__(self, message_buffer: HumanMessageBuffer, next_node: str) -> None:
        self.message_buffer = message_buffer
        self.next_node = next_node

    @property
    def node_type(self) -> Literal["LLM", "DETERMINISTIC"]:
        return "DETERMINISTIC"

    async def execute(self, state: GraphState) -> Tuple[GraphState, str]:
        raw_message = state.current_input

        unified_message = await self.message_buffer.enqueue_and_wait(
            tenant_id=state.tenant_id,
            phone_number=state.phone_number,
            message=raw_message,
        )

        if unified_message is None:
            logger.info("[FSM - TurnPredictorNode] Mensagem agrupada em buffer. Encerrando corrotina parcial.")
            return state, "END_BUFFERED"

        # Verifica se o TurnPredictor bateu no Fast-Path (Python local) ou chamou o Gemini
        usou_fast_path = unified_message.strip().endswith("?") or unified_message.lower().strip() in {
            "oi", "oii", "oiii", "ola", "olá", "opa", "bom dia", "boa tarde", "boa noite", "tudo bem"
        }
        tipo_efetivo: Literal["LLM", "DETERMINISTIC"] = "DETERMINISTIC" if usou_fast_path else "LLM"

        # Registra uso de tokens (se DETERMINISTIC, contabiliza 0 tokens; se LLM, aproxima por chars / 4)
        state.record_node_usage(
            node_name="TurnPredictorNode",
            node_type=tipo_efetivo,
            input_text=unified_message,
            output_text='{"status": "COMPLETE"}' if not usou_fast_path else "",
        )

        state.current_input = unified_message
        state.messages.append({"role": "user", "content": unified_message})
        logger.info(f"[FSM - TurnPredictorNode] Turno completo ({tipo_efetivo}). Avançando para '{self.next_node}'.")
        return state, self.next_node