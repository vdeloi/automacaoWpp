# app/infrastructure/nodes/judge_node.py
import json
from typing import Literal, Tuple
from google import genai
from google.genai import types

from app.core.logger import logger
from app.domain.interfaces import IGraphNode
from app.domain.models import GraphState


class JudgeEvaluatorNode(IGraphNode):
    """
    Nó Avaliador em tempo real (Tipo: LLM) com Circuit Breaker integrado.
    Interrompe loops de alucinação após 'max_retries' reprovações consecutivas.
    """

    def __init__(
        self,
        client: genai.Client,
        evaluation_rules: str,
        retry_node: str,
        model_name: str,
        success_node: str = "END_SUCCESS",
        handoff_node: str = "END_HANDOFF",
        max_retries: int = 2,
    ) -> None:
        self.client = client
        self.evaluation_rules = evaluation_rules
        self.retry_node = retry_node
        self.model_name = model_name
        self.success_node = success_node
        self.handoff_node = handoff_node
        self.max_retries = max_retries

    @property
    def node_type(self) -> Literal["LLM", "DETERMINISTIC"]:
        return "LLM"

    async def execute(self, state: GraphState) -> Tuple[GraphState, str]:
        tools_called = state.internal_scratchpad.get("last_tools_called", [])
        all_tools_called = state.internal_scratchpad.get("tools_called", [])

        system_prompt = (
            "Você é um Auditor Rigoroso de Qualidade de IA (LLM-as-a-Judge).\n"
            "Sua missão é avaliar se a resposta gerada pelo Agente Especialista respeita as regras abaixo:\n"
            f"{self.evaluation_rules}\n\n"
            "Responda ESTRITAMENTE com um JSON válido no formato:\n"
            '{"aprovado": true/false, "critica": "Explicação clara do que o agente deve corrigir (vazio se aprovado)"}'
        )

        prompt_auditoria = (
            f'Mensagem do Cliente: "{state.current_input}"\n'
            f"Ferramentas executadas neste turno: {tools_called}\n"
            f"Todas as ferramentas executadas no fluxo atual: {all_tools_called}\n"
            f'Resposta Proposta pelo Agente: "{state.final_response}"'
        )

        texto_entrada_completo = f"{system_prompt}\n{prompt_auditoria}"

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt_auditoria,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.0,
                    max_output_tokens=150,
                    response_mime_type="application/json",
                ),
            )
            texto_saida = response.text or "{}"

            state.record_node_usage(
                node_name="JudgeEvaluatorNode",
                node_type=self.node_type,
                input_text=texto_entrada_completo,
                output_text=texto_saida,
            )

            resultado = json.loads(texto_saida)
            aprovado = bool(resultado.get("aprovado", True))
            critica = str(resultado.get("critica", "")).strip()

            if not aprovado and critica:
                state.errors_feedback.append(critica)
                tentativas = len(state.errors_feedback)

                # Circuit Breaker: evita loop infinito e queima de tokens
                if tentativas >= self.max_retries:
                    logger.error(
                        f"[FSM - JudgeEvaluatorNode] Circuit Breaker acionado após {tentativas} reprovações! "
                        f"Última crítica: '{critica}'. Encaminhando para '{self.handoff_node}'."
                    )
                    state.errors_feedback.clear()
                    state.final_response = (
                        "Peço um instante, por favor! Vou transferir seu atendimento para um de nossos "
                        "atendentes humanos confirmar essa informação com segurança para você."
                    )
                    return state, self.handoff_node

                logger.warning(
                    f"[FSM - JudgeEvaluatorNode] REPROVADO ({tentativas}/{self.max_retries})! "
                    f"Crítica: '{critica}'. Devolvendo para '{self.retry_node}'."
                )
                return state, self.retry_node

            state.errors_feedback.clear()
            logger.info(f"[FSM - JudgeEvaluatorNode] APROVADO! Seguindo para '{self.success_node}'.")
            return state, self.success_node

        except Exception as err:
            erro_str = str(err)
            if "429" in erro_str or "RESOURCE_EXHAUSTED" in erro_str:
                logger.warning(
                    "[FSM - JudgeEvaluatorNode] Cota por minuto excedida (429). "
                    "Liberando resposta pelo fallback."
                )
            else:
                logger.error(f"[FSM - JudgeEvaluatorNode] Falha na avaliação: {err}")
            return state, self.success_node