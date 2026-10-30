# app/infrastructure/llm/judge_logger.py
import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict
from google import genai
from app.core.logger import logger
from app.domain.interfaces import AgentResponse


class LLMAuditLogger:
    """Avalia alucinações (LLM-as-a-Judge) de forma 100% assíncrona em background."""

    def __init__(
        self,
        client: genai.Client,
        judge_model_name: str = "gemini-2.5-flash",
        logs_dir: str = "logs/debug_interacoes",
    ) -> None:
        self.client = client
        self.judge_model_name = judge_model_name
        self.logs_dir = logs_dir

    def _write_json_sync(self, dados: Dict[str, Any]) -> str:
        os.makedirs(self.logs_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filepath = os.path.join(self.logs_dir, f"log_{timestamp}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=4, ensure_ascii=False)
        return filepath

    async def evaluate_and_log(
        self,
        user_input: str,
        agent_response: AgentResponse,
        elapsed_seconds: float,
        rag_used: bool,
    ) -> None:
        dados_log: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "tempo_total_segundos": round(elapsed_seconds, 3),
            "tokens": {
                "input": agent_response.input_tokens,
                "output": agent_response.output_tokens,
                "total": agent_response.input_tokens + agent_response.output_tokens,
            },
            "ferramentas_utilizadas": agent_response.tools_called,
            "rag_utilizado": rag_used,
            "pergunta_usuario": user_input,
            "resposta_agente": agent_response.text_output,
        }

        prompt = (
            "Você é um avaliador de qualidade de IA (LLM-as-a-Judge).\n"
            "Verifique se a 'Resposta do Agente' responde à 'Pergunta do Usuário' de forma factual, "
            "sem inventar informações (alucinação) ou prometer coisas fora de contexto.\n\n"
            f'Pergunta do Usuário: "{user_input}"\n'
            f'Resposta do Agente: "{agent_response.text_output}"\n\n'
            'Responda APENAS com um JSON válido: {"alucinou": true/false, "justificativa": "breve explicação"}'
        )

        try:
            response = await self.client.aio.models.generate_content(
                model=self.judge_model_name,
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0.0},
            )
            dados_log["avaliacao_alucinacao"] = json.loads(response.text or "{}")
        except Exception as err:
            dados_log["avaliacao_alucinacao"] = {"erro": f"Falha ao avaliar: {err}"}

        filepath = await asyncio.to_thread(self._write_json_sync, dados_log)
        logger.info(f"Log de auditoria salvo em {filepath}")