# app/services/chat_orchestrator.py
import asyncio
import json
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from app.core.logger import ctx_phone, ctx_tenant_id, logger
from app.domain.interfaces import (
    IDashboardRepository,
    IGraphNode,
    IMessagingGateway,
    ISessionRepository,
    ITurnPredictor,
    IncomingMessage,
)
from app.domain.models import GraphState
from app.models.chat import ChatResponse
from app.tenants.registry import TenantRegistry


class ChatOrchestratorService:
    """
    Motor de Execução de Grafos (State Machine Engine).
    Inclui telemetria de tokens por aproximação de caracteres (1 token = 3.8 caracteres).
    """

    TERMINAL_NODES = {"END", "END_SUCCESS", "END_BUFFERED", "END_HANDOFF"}

    def __init__(
        self,
        tenant_registry: TenantRegistry,
        session_repo: ISessionRepository,
        turn_predictor: ITurnPredictor,
        messaging_gateway: Optional[IMessagingGateway] = None,
        max_iterations: int = 10,
        enable_debug_logs: bool = False,
        logs_dir: str = "logs/debug_interacoes",
        dashboard_repo: Optional[IDashboardRepository] = None,
    ) -> None:
        self.tenant_registry = tenant_registry
        self.session_repo = session_repo
        self.turn_predictor = turn_predictor
        self.messaging_gateway = messaging_gateway
        self.max_iterations = max_iterations
        self.enable_debug_logs = enable_debug_logs
        self.logs_dir = logs_dir
        self.dashboard_repo = dashboard_repo
        self._locks: Dict[Tuple[str, str], asyncio.Lock] = {}
        self._lock_waiters: Dict[Tuple[str, str], int] = {}

    def _write_debug_json_sync(self, payload: Dict[str, Any]) -> str:
        """Salva o relatório de tokens em disco de forma organizada."""
        os.makedirs(self.logs_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filepath = os.path.join(self.logs_dir, f"fsm_tokens_{timestamp}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4, ensure_ascii=False)
        return filepath

    async def _save_debug_token_report(self, state: GraphState, elapsed_seconds: float) -> None:
        """Gera e persiste o resumo de consumo de tokens de todos os nós (apenas em modo debug)."""
        if not self.enable_debug_logs or not state.debug_mode:
            return
        resumo_tokens = state.get_tokens_summary()
        totais = resumo_tokens["tokens_estimados_totais"]
        logger.info(
            f"[DEBUG TOKENS] Resumo da Execução | "
            f"Input: ~{totais['input']} tk | Output: ~{totais['output']} tk | "
            f"Total: ~{totais['total_geral']} tk (1 tk = {GraphState.CHARS_PER_TOKEN} chars)"
        )
        relatorio_completo: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "tenant_id": state.tenant_id,
            "phone_number": state.phone_number,
            "tempo_total_segundos": round(elapsed_seconds, 3),
            "pergunta_usuario": state.current_input,
            "resposta_final": state.final_response,
            "rascunho_interno_blackboard": state.internal_scratchpad,
            "telemetria_tokens": resumo_tokens,
        }
        filepath = await asyncio.to_thread(self._write_debug_json_sync, relatorio_completo)
        logger.info(f"[DEBUG TOKENS] Relatório detalhado salvo em: {filepath}")

    async def _run_graph_loop(
        self,
        graph: Dict[str, IGraphNode],
        start_node: str,
        state: GraphState,
    ) -> Tuple[GraphState, str]:
        """Executa o loop da Máquina de Estados com limite máximo de transições."""
        current_node = start_node
        iterations = 0
        while current_node not in self.TERMINAL_NODES:
            if iterations >= self.max_iterations:
                logger.error(
                    f"[FSM Engine] Limite máximo de iterações ({self.max_iterations}) atingido! "
                    "Acionando Transbordo Humano de segurança."
                )
                if "transbordo_humano" in graph:
                    state, _ = await graph["transbordo_humano"].execute(state)
                else:
                    state.final_response = (
                        "Peço desculpas pela demora! Estou transferindo seu atendimento para um "
                        "especialista humano que já vai te responder por aqui."
                    )
                return state, "END_HANDOFF"

            node_instance = graph.get(current_node)
            if not node_instance:
                logger.error(f"[FSM Engine] Nó '{current_node}' não encontrado no grafo do tenant '{state.tenant_id}'.")
                state.final_response = "Desculpe, ocorreu um erro interno no fluxo de atendimento."
                return state, "END"

            iterations += 1
            logger.info(
                f"[FSM Engine] Executando nó '{current_node}' [Tipo: {node_instance.node_type}] "
                f"(Iteração {iterations}/{self.max_iterations})"
            )
            state, next_node = await node_instance.execute(state)
            current_node = next_node
        return state, current_node

    async def handle_incoming_message(self, msg: IncomingMessage) -> ChatResponse:
        t_start = time.perf_counter()
        ctx_tenant_id.set(msg.tenant_id)
        ctx_phone.set(msg.phone_number)

        tenant = self.tenant_registry.get_tenant(msg.tenant_id)
        if not tenant:
            return ChatResponse(
                response="Desculpe, o estabelecimento selecionado não foi encontrado.",
                interaction_id=None,
                status="error",
            )

        graph, entrypoint = tenant.build_graph()
        state = GraphState(
            tenant_id=msg.tenant_id,
            phone_number=msg.phone_number,
            current_input=msg.text,
            debug_mode=self.enable_debug_logs,
        )

        current_node = entrypoint
        if current_node == "preditor_turno" and "preditor_turno" in graph:
            state, current_node = await graph["preditor_turno"].execute(state)
            if current_node == "END_BUFFERED":
                return ChatResponse(response="", interaction_id=None, status="buffered")

        session_key = (msg.tenant_id, msg.phone_number)
        lock = self._locks.setdefault(session_key, asyncio.Lock())
        self._lock_waiters[session_key] = self._lock_waiters.get(session_key, 0) + 1
        try:
            async with lock:
                history = await self.session_repo.get_history(
                    session_id=msg.phone_number, tenant_id=msg.tenant_id, limit=10
                )
                previous_id = await self.session_repo.get_last_interaction_id(
                    session_id=msg.phone_number, tenant_id=msg.tenant_id
                )
                state.messages = history + [{"role": "user", "content": state.current_input}]
                state.interaction_id = previous_id

                await self.session_repo.save_message(
                    session_id=msg.phone_number,
                    tenant_id=msg.tenant_id,
                    role="user",
                    content=state.current_input,
                )

                state, terminal_status = await self._run_graph_loop(
                    graph=graph,
                    start_node=current_node,
                    state=state,
                )

                if terminal_status == "END_BUFFERED":
                    return ChatResponse(response="", interaction_id=None, status="buffered")

                if state.interaction_id:
                    await self.session_repo.save_interaction_id(
                        session_id=msg.phone_number,
                        tenant_id=msg.tenant_id,
                        interaction_id=state.interaction_id,
                    )

                await self.session_repo.save_message(
                    session_id=msg.phone_number,
                    tenant_id=msg.tenant_id,
                    role="assistant",
                    content=state.final_response,
                )
        finally:
            self._lock_waiters[session_key] -= 1
            if self._lock_waiters[session_key] <= 0:
                self._locks.pop(session_key, None)
                self._lock_waiters.pop(session_key, None)

        elapsed = time.perf_counter() - t_start

        # Persiste telemetria no banco para visualização imediata no Dashboard GUI
        if self.dashboard_repo:
            asyncio.create_task(self.dashboard_repo.save_execution_telemetry(state, elapsed))

        # Se 'enable_debug_logs' estiver ativo, gera e salva o relatório completo em disco
        if self.enable_debug_logs:
            asyncio.create_task(self._save_debug_token_report(state, elapsed))

        typing_delay = self.turn_predictor.calculate_typing_delay(state.final_response)
        if self.messaging_gateway:
            await self.messaging_gateway.send_typing_indicator(msg.tenant_id, msg.phone_number, typing_delay)
            await self.messaging_gateway.send_text_message(msg.tenant_id, msg.phone_number, state.final_response)
        else:
            await asyncio.sleep(typing_delay)

        return ChatResponse(
            response=state.final_response,
            interaction_id=state.interaction_id,
            status="success",
        )