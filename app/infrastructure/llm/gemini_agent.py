# app/infrastructure/llm/gemini_agent.py
import asyncio
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional
from google import genai

from app.core.logger import logger
from app.domain.interfaces import AgentResponse, AppTool, ILLMAgent

AuditCallback = Callable[[str, AgentResponse, float, bool], Coroutine[Any, Any, None]]


class GeminiInteractionsAgent(ILLMAgent):
    """Implementação resiliente do ILLMAgent usando o cliente assíncrono nativo (client.aio) do Google GenAI."""

    def __init__(
        self,
        client: genai.Client,
        model_name: str,
        audit_callback: Optional[AuditCallback] = None,
    ) -> None:
        self.client = client
        self.model_name = model_name
        self.audit_callback = audit_callback

    def _build_gemini_tools(
        self, tools: Optional[List[AppTool]], knowledge_store_id: Optional[str]
    ) -> List[Dict[str, Any]]:
        schemas = [t.to_json_schema() for t in (tools or [])]
        if knowledge_store_id:
            schemas.append({
                "type": "file_search",
                "file_search_store_names": [knowledge_store_id],
            })
        return schemas

    def _extract_text(self, raw_response: Any) -> str:
        if not raw_response:
            return "Olá! Como posso ajudar você hoje?"

        for attr in ("output_text", "text"):
            val = getattr(raw_response, attr, None)
            if val:
                return str(val).strip()

        textos: List[str] = []
        for step in getattr(raw_response, "steps", []) or []:
            if "thought" in str(getattr(step, "type", "")).lower():
                continue
            if getattr(step, "text", None):
                textos.append(str(step.text).strip())

            content = getattr(step, "content", None)
            if content:
                blocks = content if isinstance(content, list) else [content]
                for block in blocks:
                    if "thought" in str(getattr(block, "type", "")).lower():
                        continue
                    text = getattr(block, "text", None) or (
                        block.get("text") if isinstance(block, dict) else None
                    )
                    if text:
                        textos.append(str(text).strip())

        return "\n".join(filter(None, textos)).strip() or "Desculpe, poderia repetir?"

    async def _execute_single_tool_safe(
        self,
        fc_step: Any,
        tool_map: Dict[str, AppTool],
        tools_executed: List[str],
    ) -> Optional[Dict[str, Any]]:
        """Executa uma ferramenta isolando qualquer falha para não quebrar chamadas paralelas."""
        name = getattr(fc_step, "name", None)
        args = getattr(fc_step, "arguments", {}) or {}
        call_id = getattr(fc_step, "id", None)

        if not name or name not in tool_map:
            return None

        tools_executed.append(name)
        try:
            res = await tool_map[name].execute_async(**args)
            output_payload: Dict[str, Any] = {"output": str(res)}
        except Exception as tool_err:
            logger.error(f"[TOOL ERROR] Falha ao executar ferramenta '{name}': {tool_err}", exc_info=True)
            output_payload = {
                "error": f"Falha temporária ao executar a ferramenta '{name}': {tool_err}"
            }

        return {
            "type": "function_result",
            "name": name,
            "call_id": call_id,
            "result": output_payload,
        }

    async def generate_interaction(
        self,
        user_input: str,
        system_instruction: str,
        previous_interaction_id: Optional[str] = None,
        tools: Optional[List[AppTool]] = None,
        knowledge_store_id: Optional[str] = None,
        max_turns: int = 5,
    ) -> AgentResponse:
        t_start = time.perf_counter()
        tools_executed: List[str] = []
        current_id = previous_interaction_id
        tool_map = {t.name: t for t in (tools or [])}
        gemini_tools = self._build_gemini_tools(tools, knowledge_store_id)

        kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "input": user_input,
            "system_instruction": system_instruction,
            "store": True,
        }
        if previous_interaction_id:
            kwargs["previous_interaction_id"] = previous_interaction_id
        if gemini_tools:
            kwargs["tools"] = gemini_tools

        try:
            raw_response = await self.client.aio.interactions.create(**kwargs)
            current_id = getattr(raw_response, "id", current_id)

            for _ in range(max_turns):
                steps = getattr(raw_response, "steps", []) or []
                status = getattr(raw_response, "status", None)
                fc_steps = [
                    s for s in steps if "function_call" in str(getattr(s, "type", "")).lower()
                ]

                if (not fc_steps and status != "requires_action") or not tool_map:
                    break

                raw_results = await asyncio.gather(
                    *(self._execute_single_tool_safe(s, tool_map, tools_executed) for s in fc_steps),
                    return_exceptions=True,
                )

                results: List[Dict[str, Any]] = []
                for item in raw_results:
                    if isinstance(item, Exception):
                        logger.error(f"Exceção inesperada no gather de ferramentas: {item}")
                    elif isinstance(item, dict):
                        results.append(item)

                if not results:
                    break

                raw_response = await self.client.aio.interactions.create(
                    model=self.model_name,
                    input=results,
                    previous_interaction_id=current_id,
                    system_instruction=system_instruction,
                    tools=gemini_tools,
                    store=True,
                )
                current_id = getattr(raw_response, "id", current_id)

            output_text = self._extract_text(raw_response)
            usage = getattr(raw_response, "usage_metadata", None)
            in_tokens = getattr(usage, "prompt_token_count", 0) if usage else 0
            out_tokens = getattr(usage, "candidates_token_count", 0) if usage else 0

            agent_resp = AgentResponse(
                text_output=output_text,
                interaction_id=current_id,
                tools_called=tools_executed,
                input_tokens=in_tokens,
                output_tokens=out_tokens,
                raw_response=raw_response,
            )

            elapsed = time.perf_counter() - t_start
            logger.info(f"Interação concluída em {elapsed:.2f}s | Tools: {tools_executed}")

            if self.audit_callback:
                asyncio.create_task(
                    self.audit_callback(user_input, agent_resp, elapsed, bool(knowledge_store_id))
                )

            return agent_resp

        except Exception as err:
            logger.error(f"Falha no processamento do LLM: {err}", exc_info=True)
            return AgentResponse(
                text_output="Desculpe, ocorreu uma instabilidade temporária ao consultar as informações.",
                interaction_id=previous_interaction_id,
                tools_called=tools_executed,
            )