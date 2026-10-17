# app/services/gemini_agent.py
import asyncio
import time
from typing import Any, Dict, List, Optional

from google import genai
from app.services.base_agent import BaseLLMAgent, AgentResponse
from app.tools.base_tool import AppTool


class GeminiInteractionsAgent(BaseLLMAgent):
    """
    Agente inteligente 100% concorrente e Stateless com suporte a Function Calling e RAG.
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.5-flash",
        default_system_instruction: Optional[str] = None,
    ) -> None:
        super().__init__(
            model_name=model_name,
            default_system_instruction=default_system_instruction,
        )
        # O cliente genai é thread-safe para leituras/chamadas de API
        self.client = genai.Client(api_key=api_key)

    def _extract_text_from_interaction(self, raw_response: Any) -> str:
        """Extrai o texto final gerado pelo modelo a partir dos steps da resposta."""
        if not raw_response:
            return "Olá! Como posso ajudar você hoje?"

        if hasattr(raw_response, "output_text") and raw_response.output_text:
            return str(raw_response.output_text).strip()

        if hasattr(raw_response, "text") and raw_response.text:
            return str(raw_response.text).strip()

        textos: List[str] = []
        steps = getattr(raw_response, "steps", []) or []

        for step in steps:
            step_type = str(getattr(step, "type", "")).lower()
            if "thought" in step_type:
                continue

            if hasattr(step, "text") and step.text:
                textos.append(str(step.text).strip())

            content = getattr(step, "content", None)
            if content:
                blocks = content if isinstance(content, list) else [content]
                for block in blocks:
                    block_type = str(getattr(block, "type", "")).lower()
                    if "thought" in block_type:
                        continue

                    if hasattr(block, "text") and block.text:
                        textos.append(str(block.text).strip())
                    elif isinstance(block, dict) and block.get("text"):
                        textos.append(str(block["text"]).strip())

                    annotations = getattr(block, "annotations", []) or []
                    for annotation in annotations:
                        annot_type = str(getattr(annotation, "type", "")).lower()
                        if "citation" in annot_type:
                            nome_arq = getattr(annotation, "file_name", "documento")
                            pag = getattr(annotation, "page_number", None)
                            print(f"[RAG CITAÇÃO] Arquivo: {nome_arq}" + (f" | Pág: {pag}" if pag else ""))

        if textos:
            return "\n".join([t for t in textos if t]).strip()

        return "Desculpe, não consegui processar a resposta completa. Poderia repetir?"

    async def generate_interaction(
        self,
        user_input: str,
        system_instruction: Optional[str] = None,
        previous_interaction_id: Optional[str] = None,
        tool_schemas: Optional[List[Dict[str, Any]]] = None,
        tool_map: Optional[Dict[str, AppTool]] = None,
        store: bool = True,
        max_turns: int = 5,
        **kwargs: Any,
    ) -> AgentResponse:
        """
        Executa o ciclo completo de interação de forma assíncrona e isolada por requisição.
        """
        inicio_total = time.perf_counter()
        tools_executed: List[str] = []
        current_id = previous_interaction_id

        # Usa a instrução passada na requisição (isolamento de tenant) ou o fallback padrão
        effective_instruction = system_instruction or self.default_system_instruction

        interaction_kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "input": user_input,
            "store": store,
        }

        if previous_interaction_id:
            interaction_kwargs["previous_interaction_id"] = previous_interaction_id
        if effective_instruction:
            interaction_kwargs["system_instruction"] = effective_instruction
        if tool_schemas:
            interaction_kwargs["tools"] = tool_schemas

        try:
            # 1. Primeira chamada ao Gemini executada em thread para não bloquear o Event Loop
            t0 = time.perf_counter()
            raw_response = await asyncio.to_thread(
                self.client.interactions.create, **interaction_kwargs
            )
            t1 = time.perf_counter()
            print(f"[PERFORMANCE] Chamada Gemini: {t1 - t0:.2f}s")

            current_id = getattr(raw_response, "id", current_id)

            # 2. Laço para resolução de ferramentas
            turn_count = 0
            while turn_count < max_turns:
                steps = getattr(raw_response, "steps", []) or []
                status = getattr(raw_response, "status", None)

                function_call_steps = [
                    s for s in steps if "function_call" in str(getattr(s, "type", "")).lower()
                ]

                if not function_call_steps and status != "requires_action":
                    break

                if not tool_map:
                    break

                t_tool_inicio = time.perf_counter()

                # Função auxiliar para rodar cada ferramenta em thread separada
                async def _run_single_tool(fc_step: Any) -> Optional[Dict[str, Any]]:
                    func_name = getattr(fc_step, "name", None)
                    func_args = getattr(fc_step, "arguments", {}) or {}
                    call_id = getattr(fc_step, "id", None)

                    if func_name and func_name in tool_map:
                        tools_executed.append(func_name)
                        app_tool = tool_map[func_name]
                        # Executa a tool síncrona (SQLite, Geopy, etc.) sem travar o loop assíncrono
                        resultado_execucao = await asyncio.to_thread(
                            app_tool.execute, **func_args
                        )
                        return {
                            "type": "function_result",
                            "name": func_name,
                            "call_id": call_id,
                            "result": {"output": str(resultado_execucao)},
                        }
                    return None

                # Executa todas as ferramentas solicitadas no turno em paralelo
                raw_results = await asyncio.gather(
                    *(_run_single_tool(step) for step in function_call_steps)
                )
                function_results = [r for r in raw_results if r is not None]

                t_tool_fim = time.perf_counter()
                print(f"[PERFORMANCE] Execução Tool Local: {(t_tool_fim - t_tool_inicio) * 1000:.2f}ms")

                if not function_results:
                    break

                # Monta os argumentos de retorno mantendo o system_instruction do tenant!
                followup_kwargs: Dict[str, Any] = {
                    "model": self.model_name,
                    "input": function_results,
                    "previous_interaction_id": current_id,
                    "tools": tool_schemas,
                    "store": store,
                }
                if effective_instruction:
                    followup_kwargs["system_instruction"] = effective_instruction

                # Envia o resultado de volta ao Gemini de forma não-bloqueante
                t2 = time.perf_counter()
                raw_response = await asyncio.to_thread(
                    self.client.interactions.create, **followup_kwargs
                )
                t3 = time.perf_counter()
                print(f"[PERFORMANCE] Chamada Gemini (Resposta Final): {t3 - t2:.2f}s")

                current_id = getattr(raw_response, "id", current_id)
                turn_count += 1

        except Exception as error:
            print(f"[ERRO GENAI] Falha no processamento: {error}")
            return AgentResponse(
                text_output="Desculpe, ocorreu uma instabilidade temporária ao consultar as informações.",
                interaction_id=previous_interaction_id,
                tools_called=tools_executed,
                raw_response=None,
            )
        else:
            output_text = self._extract_text_from_interaction(raw_response)
            fim_total = time.perf_counter()
            print(f"[PERFORMANCE] TEMPO TOTAL: {fim_total - inicio_total:.2f}s\n")

            return AgentResponse(
                text_output=output_text,
                interaction_id=current_id,
                tools_called=tools_executed,
                raw_response=raw_response,
            )