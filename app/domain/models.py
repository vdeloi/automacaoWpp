# app/domain/models.py
import math
from typing import Any, ClassVar, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class NodeExecutionMetric(BaseModel):
    """
    Armazena as métricas de execução de um único nó durante o fluxo do grafo.
    Diferencia nós baseados em LLM de nós determinísticos (Python/ML local).
    """
    node_name: str = Field(..., description="Nome identificador do nó executado.")
    node_type: Literal["LLM", "DETERMINISTIC"] = Field(
        ..., description="Tipo do nó: 'LLM' (consome tokens) ou 'DETERMINISTIC' (código local)."
    )
    input_chars: int = Field(default=0, description="Quantidade de caracteres recebidos na entrada.")
    output_chars: int = Field(default=0, description="Quantidade de caracteres gerados na saída.")
    estimated_input_tokens: int = Field(default=0, description="Estimativa de tokens de entrada (chars / 3.8).")
    estimated_output_tokens: int = Field(default=0, description="Estimativa de tokens de saída (chars / 3.8).")
    total_estimated_tokens: int = Field(default=0, description="Soma dos tokens de entrada e saída deste nó.")


class GraphState(BaseModel):
    """
    Padrão Blackboard (Estado Compartilhado).
    Armazena todo o contexto da conversa e as métricas de telemetria enquanto trafega pelo grafo.
    """
    # Constante da razão de caracteres por token solicitada para o proxy (1 token ~= 3.8 caracteres)
    CHARS_PER_TOKEN: ClassVar[float] = 3.8

    tenant_id: str = Field(
        ...,
        description="Identificador único do estabelecimento (ex: 'padaria_01', 'clinica_01').",
    )
    phone_number: str = Field(
        ...,
        description="Número de telefone do cliente usado como identificador de sessão.",
    )
    messages: List[Dict[str, str]] = Field(
        default_factory=list,
        description="Histórico de mensagens no formato [{'role': 'user'|'assistant', 'content': '...'}].",
    )
    current_input: str = Field(
        default="",
        description="Mensagem atual enviada pelo usuário (após consolidação do buffer).",
    )
    interaction_id: Optional[str] = Field(
        default=None,
        description="ID da interação mantido pela Interactions API do Gemini para continuidade de contexto.",
    )
    internal_scratchpad: Dict[str, Any] = Field(
        default_factory=dict,
        description="Rascunho compartilhado para agentes anotarem intenções, ferramentas usadas e metadados.",
    )
    errors_feedback: List[str] = Field(
        default_factory=list,
        description="Lista de críticas adicionadas pelo Nó Juiz para forçar a autocorreção do Agente Especialista.",
    )
    final_response: str = Field(
        default="",
        description="Texto final validado que será enviado de volta ao usuário no WhatsApp.",
    )

    # --- Campos para Telemetria e Contagem de Tokens ---
    debug_mode: bool = Field(
        default=False,
        description="Se True, gera também relatórios JSON detalhados em disco.",
    )
    debug_metrics: List[NodeExecutionMetric] = Field(
        default_factory=list,
        description="Lista cronológica com o consumo de tokens de cada nó visitado.",
    )

    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        """
        Calcula a aproximação de tokens onde 1 token = 3.8 caracteres.
        Usa arredondamento para cima (math.ceil) para não zerar textos curtos.
        """
        if not text:
            return 0
        return math.ceil(len(text) / cls.CHARS_PER_TOKEN)

    def record_node_usage(
        self,
        node_name: str,
        node_type: Literal["LLM", "DETERMINISTIC"],
        input_text: str = "",
        output_text: str = "",
    ) -> None:
        """
        Regista o consumo do nó em memória para alimentar o Dashboard e os logs de debug.
        Se o nó for 'DETERMINISTIC' (Python puro), os tokens estimados de LLM são zerados.
        """
        in_chars = len(input_text) if input_text else 0
        out_chars = len(output_text) if output_text else 0

        if node_type == "LLM":
            in_tokens = self.estimate_tokens(input_text)
            out_tokens = self.estimate_tokens(output_text)
        else:
            in_tokens = 0
            out_tokens = 0

        metric = NodeExecutionMetric(
            node_name=node_name,
            node_type=node_type,
            input_chars=in_chars,
            output_chars=out_chars,
            estimated_input_tokens=in_tokens,
            estimated_output_tokens=out_tokens,
            total_estimated_tokens=in_tokens + out_tokens,
        )
        self.debug_metrics.append(metric)

    def get_tokens_summary(self) -> Dict[str, Any]:
        """Gera um resumo consolidado do consumo de tokens de toda a execução do grafo."""
        total_in = sum(m.estimated_input_tokens for m in self.debug_metrics)
        total_out = sum(m.estimated_output_tokens for m in self.debug_metrics)
        llm_nodes_count = sum(1 for m in self.debug_metrics if m.node_type == "LLM")
        det_nodes_count = sum(1 for m in self.debug_metrics if m.node_type == "DETERMINISTIC")

        return {
            "regra_aproximacao": f"1 token = {self.CHARS_PER_TOKEN} caracteres",
            "nos_executados": {
                "total": len(self.debug_metrics),
                "agentes_llm": llm_nodes_count,
                "nos_deterministicos_python": det_nodes_count,
            },
            "tokens_estimados_totais": {
                "input": total_in,
                "output": total_out,
                "total_geral": total_in + total_out,
            },
            "detalhamento_por_no": [m.model_dump() for m in self.debug_metrics],
        }