# app/infrastructure/nodes/router_node.py
import json
from typing import Dict, Literal, Tuple
from google import genai
from google.genai import types

from app.core.logger import logger
from app.domain.interfaces import IGraphNode
from app.domain.models import GraphState


class RouterNode(IGraphNode):
    """
    Nó Roteador de Intenção (Tipo: LLM).
    Exige injeção explícita do modelo classificador via construtor.
    """

    def __init__(
        self,
        client: genai.Client,
        routes_description: Dict[str, str],
        default_route: str,
        model_name: str,
    ) -> None:
        self.client = client
        self.routes_description = routes_description
        self.default_route = default_route
        self.model_name = model_name

    @property
    def node_type(self) -> Literal["LLM", "DETERMINISTIC"]:
        return "LLM"

    async def execute(self, state: GraphState) -> Tuple[GraphState, str]:
        opcoes_formatadas = "\n".join(
            f'- "{node_name}": {desc}' for node_name, desc in self.routes_description.items()
        )
        chaves_validas = list(self.routes_description.keys())

        system_prompt = (
            "Você é um roteador de atendimento inteligente.\n"
            "Analise a mensagem do cliente e escolha a rota mais adequada entre as opções abaixo:\n"
            f"{opcoes_formatadas}\n\n"
            "Responda APENAS com um JSON válido no formato:\n"
            '{"intencao": "resumo curto da intencao", "destino": "nome_exato_da_rota"}'
        )

        historico_recente = "\n".join(
            f"{m['role']}: {m['content']}" for m in state.messages[-4:]
        )
        prompt = f"Histórico recente:\n{historico_recente}\n\nÚltima mensagem: {state.current_input}"
        texto_entrada_completo = f"{system_prompt}\n{prompt}"

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.0,
                    max_output_tokens=100,
                    response_mime_type="application/json",
                ),
            )
            texto_saida = response.text or "{}"

            state.record_node_usage(
                node_name="RouterNode",
                node_type=self.node_type,
                input_text=texto_entrada_completo,
                output_text=texto_saida,
            )

            dados = json.loads(texto_saida)
            intencao = dados.get("intencao", "geral")
            destino = dados.get("destino", self.default_route)

            if destino not in chaves_validas:
                destino = self.default_route

            state.internal_scratchpad["intent"] = intencao
            state.internal_scratchpad["routed_to"] = destino

            logger.info(f"[FSM - RouterNode] Intenção='{intencao}' -> Próximo Nó='{destino}'")
            return state, destino

        except Exception as err:
            logger.warning(f"[FSM - RouterNode] Erro ao rotear ({err}). Usando rota padrão '{self.default_route}'.")
            state.internal_scratchpad["intent"] = "fallback"
            state.internal_scratchpad["routed_to"] = self.default_route
            return state, self.default_route