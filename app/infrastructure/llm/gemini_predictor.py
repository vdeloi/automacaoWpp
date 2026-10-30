# app/infrastructure/llm/gemini_predictor.py
import asyncio
import json
import re
from typing import List, Optional
from google import genai
from google.genai import types
from app.core.logger import logger
from app.domain.interfaces import ITurnPredictor, TurnPrediction


class GeminiTurnPredictor(ITurnPredictor):
    """Implementação 100% assíncrona do classificador de fim de turno usando client.aio."""

    SAUDACOES_ISOLADAS = {
        "oi", "oii", "oiii", "ola", "olá", "opa", "bom dia",
        "boa tarde", "boa noite", "e ai", "e aí", "tudo bem", "tudobem",
    }

    SYSTEM_PROMPT = (
        "Analise as mensagens enviadas pelo cliente no WhatsApp e responda APENAS um objeto JSON válido "
        'no formato: {"status": "COMPLETE" ou "INCOMPLETE", "extra_wait_seconds": float, "reason": "string curta"}.\n'
        "- Use INCOMPLETE (extra_wait_seconds entre 6.0 e 9.0) se a frase parecer cortada no meio, terminar em "
        "preposição/conjunção ('quero agendar com', 'moro na rua') ou for apenas um chamado ('espera aí', 'moço').\n"
        "- Use COMPLETE (extra_wait_seconds entre 0.0 e 1.0) se já houver uma pergunta, pedido, endereço, nome ou data."
    )

    def __init__(self, client: genai.Client, model_name: str = "gemini-2.5-flash") -> None:
        self.client = client
        self.model_name = model_name

    def _fast_path_check(self, accumulated_messages: List[str]) -> Optional[TurnPrediction]:
        joined = "\n".join(accumulated_messages).strip()
        clean = re.sub(r"[^\w\s]", "", joined.lower()).strip()

        if len(accumulated_messages) == 1 and clean in self.SAUDACOES_ISOLADAS:
            return TurnPrediction(
                status="INCOMPLETE",
                extra_wait_seconds=7.0,
                reason="Fast-Path: Saudação isolada",
            )

        if joined.endswith("?"):
            return TurnPrediction(
                status="COMPLETE",
                extra_wait_seconds=0.5,
                reason="Fast-Path: Pergunta direta com '?'",
            )

        return None

    async def _call_gemini_async(self, prompt: str) -> str:
        try:
            response = await self.client.aio.models.generate_content(
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
            response = await self.client.aio.models.generate_content(
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
        fast_result = self._fast_path_check(accumulated_messages)
        if fast_result is not None:
            return fast_result

        joined_text = "\n".join(accumulated_messages)
        prompt = f'Mensagens do cliente:\n"""\n{joined_text}\n"""'

        try:
            raw_json = await asyncio.wait_for(
                self._call_gemini_async(prompt),
                timeout=6.0,
            )
            data = json.loads(raw_json.strip())
            prediction = TurnPrediction(**data)
            prediction.extra_wait_seconds = max(0.0, min(prediction.extra_wait_seconds, 10.0))
            return prediction
        except Exception as error:
            nome_erro = type(error).__name__
            logger.warning(f"Fallback do TurnPredictor acionado ({nome_erro}: {error})")
            return TurnPrediction(
                status="COMPLETE",
                extra_wait_seconds=1.5,
                reason=f"Fallback ({nome_erro})",
            )

    def calculate_typing_delay(self, response_text: str) -> float:
        delay = len(response_text) / 45.0
        return max(1.2, min(delay, 4.5))