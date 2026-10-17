# app/services/message_buffer.py
import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.core.config import settings


class TurnPrediction(BaseModel):
    """Esquema estruturado retornado pelo classificador de turno."""
    status: Literal["COMPLETE", "INCOMPLETE"] = Field(
        description="COMPLETE se o usuário concluiu a fala; INCOMPLETE se ainda vai digitar mais."
    )
    extra_wait_seconds: float = Field(
        description="Segundos adicionais de espera (0.0 a 1.5 para COMPLETE; 6.0 a 10.0 para INCOMPLETE)."
    )
    reason: str = Field(
        description="Justificativa curta para log."
    )


class LLMTurnPredictor:
    """
    Híbrido: Resolve casos óbvios em 0ms (Fast-Path) e usa o LLM sem Thinking
    apenas para mensagens ambíguas.
    """

    SAUDACOES_ISOLADAS = {
        "oi", "oii", "oiii", "ola", "olá", "opa", "bom dia",
        "boa tarde", "boa noite", "e ai", "e aí", "tudo bem", "tudobem"
    }

    SYSTEM_PROMPT = (
        "Analise as mensagens enviadas pelo cliente no WhatsApp e responda APENAS um objeto JSON válido "
        "no formato: {\"status\": \"COMPLETE\" ou \"INCOMPLETE\", \"extra_wait_seconds\": float, \"reason\": \"string curta\"}.\n"
        "- Use INCOMPLETE (extra_wait_seconds entre 6.0 e 9.0) se a frase parecer cortada no meio, terminar em "
        "preposição/conjunção ('quero agendar com', 'moro na rua') ou for apenas um chamado ('espera aí', 'moço').\n"
        "- Use COMPLETE (extra_wait_seconds entre 0.0 e 1.0) se já houver uma pergunta, pedido, endereço, nome ou data."
    )

    def __init__(self, api_key: str, model_name: str) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def _fast_path_check(self, accumulated_messages: List[str]) -> Optional[TurnPrediction]:
        """
        Verifica padrões óbvios em 0ms sem precisar chamar a rede.
        Retorna None se a frase for ambígua e precisar do LLM.
        """
        joined = "\n".join(accumulated_messages).strip()
        clean = re.sub(r"[^\w\s]", "", joined.lower()).strip()

        # 1. Apenas saudação isolada na primeira mensagem -> Espera o cliente digitar o pedido
        if len(accumulated_messages) == 1 and clean in self.SAUDACOES_ISOLADAS:
            return TurnPrediction(
                status="INCOMPLETE",
                extra_wait_seconds=7.0,
                reason="Fast-Path: Saudação isolada",
            )

        # 2. Termina com ponto de interrogação explícito -> Pergunta concluída!
        if joined.endswith("?"):
            return TurnPrediction(
                status="COMPLETE",
                extra_wait_seconds=0.5,
                reason="Fast-Path: Pergunta direta com '?'",
            )

        return None

    def _call_gemini_sync(self, prompt: str) -> str:
        """Executa a chamada ao Gemini desativando o Thinking quando suportado."""
        try:
            # Tenta com thinking_budget=0 para máxima velocidade (modelos 2.5+)
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.SYSTEM_PROMPT,
                    temperature=0.0,
                    max_output_tokens=120,
                    response_mime_type="application/json",
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            return response.text or ""
        except Exception:
            # Caso o modelo configurado não aceite thinking_config, chama com config simples
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.SYSTEM_PROMPT,
                    temperature=0.0,
                    max_output_tokens=120,
                    response_mime_type="application/json",
                ),
            )
            return response.text or ""

    async def predict(self, accumulated_messages: List[str]) -> TurnPrediction:
        """
        1º Tenta o Fast-Path (0ms).
        2º Se for ambíguo, consulta o LLM com timeout seguro de 6.0s.
        """
        # Checagem instantânea
        fast_result = self._fast_path_check(accumulated_messages)
        if fast_result is not None:
            return fast_result

        joined_text = "\n".join(accumulated_messages)
        prompt = f"Mensagens do cliente:\n\"\"\"\n{joined_text}\n\"\"\""

        try:
            raw_json = await asyncio.wait_for(
                asyncio.to_thread(self._call_gemini_sync, prompt),
                timeout=6.0,
            )

            data = json.loads(raw_json.strip())
            prediction = TurnPrediction(**data)
            prediction.extra_wait_seconds = max(0.0, min(prediction.extra_wait_seconds, 10.0))
            return prediction

        except Exception as error:
            # Agora exibe o nome exato da classe do erro (ex: TimeoutError, ClientError)
            nome_erro = type(error).__name__
            detalhe = str(error) or "Tempo limite de classificação excedido"
            print(f"[AVISO TURN PREDICTOR] Fallback acionado ({nome_erro}: {detalhe})")
            return TurnPrediction(
                status="COMPLETE",
                extra_wait_seconds=1.5,
                reason=f"Fallback ({nome_erro})",
            )

    @staticmethod
    def calculate_typing_delay(response_text: str) -> float:
        """Simula o tempo humano de digitação da resposta final (entre 1.2s e 4.5s)."""
        delay = len(response_text) / 45.0
        return max(1.2, min(delay, 4.5))


@dataclass
class UserBufferState:
    messages: List[str] = field(default_factory=list)
    generation_id: int = 0
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)


class HumanMessageBuffer:
    """
    Gerencia o agrupamento concorrente de mensagens por (tenant_id, phone_number).
    """

    MICRO_DEBOUNCE_SECONDS = 1.5

    def __init__(self, predictor: LLMTurnPredictor) -> None:
        self.predictor = predictor
        self._buffers: Dict[Tuple[str, str], UserBufferState] = {}

    def _get_state(self, tenant_id: str, phone_number: str) -> UserBufferState:
        key = (tenant_id, phone_number)
        if key not in self._buffers:
            self._buffers[key] = UserBufferState()
        return self._buffers[key]

    async def enqueue_and_wait(
        self, tenant_id: str, phone_number: str, message: str
    ) -> Optional[str]:
        state = self._get_state(tenant_id, phone_number)

        # Etapa 1: Micro-debounce inicial de 1.5s
        async with state.condition:
            state.messages.append(message.strip())
            state.generation_id += 1
            my_generation = state.generation_id

            state.condition.notify_all()

            try:
                await asyncio.wait_for(
                    state.condition.wait_for(lambda: state.generation_id != my_generation),
                    timeout=self.MICRO_DEBOUNCE_SECONDS,
                )
                return None
            except asyncio.TimeoutError:
                snapshot_messages = list(state.messages)

        # Etapa 2: Previsão (Fast-Path 0ms ou LLM rápido)
        prediction = await self.predictor.predict(snapshot_messages)
        print(
            f"[LLM TURN PREDICTOR] ({tenant_id} | {phone_number}) "
            f"Status: {prediction.status} | Espera extra: {prediction.extra_wait_seconds:.1f}s | "
            f"Motivo: {prediction.reason}"
        )

        # Etapa 3: Espera complementar caso o turno esteja incompleto
        async with state.condition:
            if state.generation_id != my_generation:
                return None

            if prediction.extra_wait_seconds > 0:
                try:
                    await asyncio.wait_for(
                        state.condition.wait_for(lambda: state.generation_id != my_generation),
                        timeout=prediction.extra_wait_seconds,
                    )
                    return None
                except asyncio.TimeoutError:
                    pass

            full_message = "\n".join(state.messages)
            state.messages.clear()
            return full_message


# Usa o mesmo modelo validado no seu .env (ou CLASSIFIER_MODEL_NAME se definido no .env)
turn_predictor = LLMTurnPredictor(
    api_key=settings.gemini_api_key,
    model_name=getattr(settings, "classifier_model_name", None) or settings.model_name,
)
message_buffer = HumanMessageBuffer(predictor=turn_predictor)