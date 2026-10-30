# app/services/message_buffer.py
import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.core.logger import logger
from app.domain.interfaces import ITurnPredictor


@dataclass
class _TurnBufferSession:
    """Encapsula o estado de acumulação de um único cliente com preempção via Future."""
    messages: List[str] = field(default_factory=list)
    active_leader_future: Optional[asyncio.Future[bool]] = None

    def preempt_current_leader(self) -> asyncio.Future[bool]:
        """Cancela a liderança da corrotina anterior e registra uma nova líder."""
        if self.active_leader_future and not self.active_leader_future.done():
            self.active_leader_future.set_result(True)

        loop = asyncio.get_running_loop()
        new_future: asyncio.Future[bool] = loop.create_future()
        self.active_leader_future = new_future
        return new_future


class HumanMessageBuffer:
    """
    Agrupa mensagens picadas por (tenant_id, phone_number) usando Preempção Determinística.
    Garante que apenas a última requisição HTTP do turno avance pelo grafo.
    """

    MICRO_DEBOUNCE_SECONDS: float = 1.5

    def __init__(self, predictor: ITurnPredictor) -> None:
        self.predictor = predictor
        self._sessions: Dict[Tuple[str, str], _TurnBufferSession] = {}

    async def _wait_for_preemption(self, future: asyncio.Future[bool], timeout: float) -> bool:
        """
        Aguarda até que uma nova mensagem chegue (retorna True)
        ou até que o tempo de silêncio expire (retorna False).
        """
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except asyncio.TimeoutError:
            return False

    async def enqueue_and_wait(self, tenant_id: str, phone_number: str, message: str) -> Optional[str]:
        key = (tenant_id, phone_number)
        session = self._sessions.get(key)
        if session is None:
            session = _TurnBufferSession()
            self._sessions[key] = session

        cleaned_msg = message.strip()
        if cleaned_msg:
            session.messages.append(cleaned_msg)

        # Assume a liderança do turno e libera imediatamente qualquer corrotina anterior como 'buffered'
        my_leader_future = session.preempt_current_leader()

        # Etapa 1: Micro-debounce inicial
        was_preempted = await self._wait_for_preemption(
            my_leader_future, timeout=self.MICRO_DEBOUNCE_SECONDS
        )
        if was_preempted or session.active_leader_future is not my_leader_future:
            return None

        snapshot = list(session.messages)

        # Etapa 2: Classificação de fim de turno via ITurnPredictor
        prediction = await self.predictor.predict(snapshot)

        # Verifica se uma nova mensagem chegou enquanto o LLM avaliava o turno
        if my_leader_future.done() or session.active_leader_future is not my_leader_future:
            return None

        logger.info(
            f"TurnPredictor: {prediction.status} (+{prediction.extra_wait_seconds:.1f}s) - {prediction.reason}"
        )

        # Etapa 3: Espera complementar caso a mensagem pareça incompleta
        if prediction.extra_wait_seconds > 0:
            was_preempted_extra = await self._wait_for_preemption(
                my_leader_future, timeout=prediction.extra_wait_seconds
            )
            if was_preempted_extra or session.active_leader_future is not my_leader_future:
                return None

        # Consolidação Atômica: apenas o líder sobrevivente remove a sessão e retorna o texto
        if self._sessions.get(key) is session:
            self._sessions.pop(key, None)

        full_message = "\n".join(session.messages).strip()
        session.messages.clear()
        return full_message or None