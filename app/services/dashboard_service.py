# app/services/dashboard_service.py
import os
import re
from typing import Any, Dict, List, Optional

from app.domain.interfaces import IDashboardRepository
from app.domain.models import GraphState
from app.services.rag_service import RAGService
from app.tenants.base import BaseTenant
from app.tenants.registry import TenantRegistry


class DashboardService:
    """
    Serviço de domínio responsável por extrair insights, topologia de grafos,
    ferramentas, RAGs e métricas de tokens (3.8 chars/token) de cada Tenant.
    """

    # Variável ajustável: define quantos caracteres equivalem a 1 token
    CHARS_PER_TOKEN: float = GraphState.CHARS_PER_TOKEN  # 3.8 caracteres por token

    def __init__(
        self,
        tenant_registry: TenantRegistry,
        dashboard_repo: IDashboardRepository,
    ) -> None:
        """
        Recebe as dependências via Injeção de Dependência (Princípio DIP do SOLID).
        """
        self.tenant_registry = tenant_registry
        self.dashboard_repo = dashboard_repo

    @staticmethod
    def _sanitize_id(raw_id: str) -> str:
        """
        Garante que o identificador do nó no Mermaid contenha apenas letras,
        números e underscore, evitando erros de sintaxe no parser 10.9.8.
        """
        clean = re.sub(r"[^a-zA-Z0-9_]", "_", str(raw_id))
        return f"NODE_{clean}"

    @staticmethod
    def _sanitize_label(text: str) -> str:
        """
        Remove caracteres especiais (como colchetes, chaves ou aspas)
        que causam 'Syntax error in text' no Mermaid 10.9.8.
        """
        if not text:
            return ""
        cleaned = text.replace('"', "'").replace("[", "(").replace("]", ")")
        cleaned = cleaned.replace("{", "(").replace("}", ")").replace(";", ",")
        return cleaned.strip()

    def list_tenants_summary(self) -> List[Dict[str, Any]]:
        """Lista todos os tenants registados com metadados básicos para o menu superior."""
        summaries: List[Dict[str, Any]] = []
        for tenant in self.tenant_registry.list_tenants():
            graph, entrypoint = tenant.build_graph()
            summaries.append({
                "tenant_id": tenant.tenant_id,
                "name": tenant.name,
                "nodes_count": len(graph),
                "tools_count": len(tenant.get_tools()),
                "has_rag": bool(tenant.file_search_store_id),
                "entrypoint": entrypoint,
            })
        return summaries

    def _inspect_graph_topology(self, tenant: BaseTenant) -> Dict[str, Any]:
        """
        Analisa polimorficamente os nós do grafo retornado por tenant.build_graph()
        e constrói a lista de nós, arestas e o diagrama Mermaid compatível com a v10.9.8.
        """
        graph, entrypoint = tenant.build_graph()
        nodes_info: List[Dict[str, Any]] = []
        edges_info: List[Dict[str, str]] = []

        # DICA DE AJUSTE: Podes trocar "graph TD" (Top-Down / Vertical)
        # por "graph LR" (Left-Right / Horizontal) se preferires o gráfico deitado!
        mermaid_lines: List[str] = [
            "graph TD",
            '    NODE_START(["Inicio WhatsApp"])',
            '    NODE_END_SUCCESS(["Fim do Atendimento"])',
        ]
        mermaid_edges: List[str] = []
        mermaid_classes: List[str] = [
            "    classDef startEnd fill:#1e293b,stroke:#0f172a,color:#ffffff,stroke-width:2px;",
            "    classDef llmNode fill:#eef2ff,stroke:#4f46e5,color:#1e1b4b,stroke-width:2px;",
            "    classDef detNode fill:#ecfdf5,stroke:#10b981,color:#064e3b,stroke-width:2px;",
            "    classDef judgeNode fill:#fffbeb,stroke:#f59e0b,color:#78350f,stroke-width:2px;",
            "    class NODE_START,NODE_END_SUCCESS startEnd;",
        ]

        start_safe_id = self._sanitize_id(entrypoint)
        mermaid_edges.append(f"    NODE_START --> {start_safe_id}")

        for step_index, (node_id, node_obj) in enumerate(graph.items(), start=1):
            safe_node_id = self._sanitize_id(node_id)
            class_name = type(node_obj).__name__
            node_type = node_obj.node_type
            node_tools = [t.name for t in getattr(node_obj, "tools", []) or []]
            uses_rag = bool(getattr(node_obj, "knowledge_store_id", None))
            outgoing_for_this_node: List[Dict[str, str]] = []

            # Identifica o papel do nó para escolher a cor e o rótulo amigável
            if "Judge" in class_name:
                style_class = "judgeNode"
                role_label = "Juiz Avaliador LLM"
            elif "Router" in class_name:
                style_class = "llmNode"
                role_label = "Roteador de Intencao LLM"
            elif node_type == "LLM":
                style_class = "llmNode"
                role_label = "Agente Especialista LLM"
            else:
                style_class = "detNode"
                role_label = "Preditor de Turno / Buffer"

            # Cria a caixa do nó usando apenas <br/> simples dentro de aspas duplas
            clean_title = self._sanitize_label(node_id)
            clean_role = self._sanitize_label(role_label)
            mermaid_lines.append(f'    {safe_node_id}["{clean_title}<br/>({clean_role})"]')
            mermaid_classes.append(f"    class {safe_node_id} {style_class};")

            # 1. Verifica se o nó possui transição direta (next_node)
            if hasattr(node_obj, "next_node"):
                target = getattr(node_obj, "next_node")
                safe_target = self._sanitize_id(target)
                edge_data = {
                    "from": node_id,
                    "to": target,
                    "label": "Fluxo Direto",
                    "description": "Avança automaticamente após processar a etapa.",
                }
                edges_info.append(edge_data)
                outgoing_for_this_node.append(edge_data)
                mermaid_edges.append(f"    {safe_node_id} --> {safe_target}")

            # 2. Verifica se o nó é um Roteador com múltiplas rotas (routes_description)
            if hasattr(node_obj, "routes_description"):
                routes: Dict[str, str] = getattr(node_obj, "routes_description", {})
                default_route = getattr(node_obj, "default_route", "")
                for route_target, desc in routes.items():
                    safe_target = self._sanitize_id(route_target)
                    is_def = route_target == default_route
                    short_label = "Rota Padrao" if is_def else "Intencao"
                    edge_data = {
                        "from": node_id,
                        "to": route_target,
                        "label": short_label,
                        "description": desc,
                    }
                    edges_info.append(edge_data)
                    outgoing_for_this_node.append(edge_data)
                    mermaid_edges.append(f"    {safe_node_id} -- {short_label} --> {safe_target}")

            # 3. Verifica se o nó é um Juiz Avaliador (retry_node e success_node)
            if hasattr(node_obj, "retry_node") and hasattr(node_obj, "success_node"):
                retry_target = getattr(node_obj, "retry_node")
                success_target = getattr(node_obj, "success_node")
                safe_retry = self._sanitize_id(retry_target)
                safe_success = self._sanitize_id(success_target)

                edge_ok = {
                    "from": node_id,
                    "to": success_target,
                    "label": "Aprovado",
                    "description": "A resposta cumpriu as regras e é enviada ao cliente.",
                }
                edge_retry = {
                    "from": node_id,
                    "to": retry_target,
                    "label": "Reprovado",
                    "description": "Retorna ao agente especialista para corrigir a resposta.",
                }
                edges_info.extend([edge_ok, edge_retry])
                outgoing_for_this_node.extend([edge_ok, edge_retry])
                mermaid_edges.append(f"    {safe_node_id} -- Aprovado --> {safe_success}")
                mermaid_edges.append(f"    {safe_node_id} -. Reprovado .-> {safe_retry}")

            # Guarda o prompt ou regras de auditoria para visualização nos cartões
            full_instruction = (
                getattr(node_obj, "system_instruction", None)
                or getattr(node_obj, "evaluation_rules", None)
                or ""
            )

            nodes_info.append({
                "step_order": step_index,
                "id": node_id,
                "class_name": class_name,
                "role_label": role_label,
                "display_name": getattr(node_obj, "node_name", node_id),
                "node_type": node_type,
                "tools": node_tools,
                "uses_rag": uses_rag,
                "outgoing_edges": outgoing_for_this_node,
                "system_instruction": full_instruction if full_instruction else None,
            })

        # Une as partes do diagrama na ordem correta exigida pelo Mermaid 10.9.8
        mermaid_diagram = "\n".join(mermaid_lines + mermaid_edges + mermaid_classes)

        return {
            "entrypoint": entrypoint,
            "nodes": nodes_info,
            "edges": edges_info,
            "mermaid_diagram": mermaid_diagram,
        }

    def _inspect_tools_and_rag(self, tenant: BaseTenant, topology_nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Extrai especificações das ferramentas (AppTool) e dos documentos RAG do tenant."""
        tools_list: List[Dict[str, Any]] = []
        for tool in tenant.get_tools():
            used_by_nodes = [n["id"] for n in topology_nodes if tool.name in n.get("tools", [])]
            tools_list.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "is_async": getattr(tool, "_is_async", True),
                "attached_nodes": used_by_nodes,
            })

        rag_dir = os.path.join("data", "tenants", tenant.tenant_id)
        local_documents: List[Dict[str, Any]] = []
        if os.path.exists(rag_dir):
            for fname in sorted(os.listdir(rag_dir)):
                fpath = os.path.join(rag_dir, fname)
                ext = os.path.splitext(fname)[1].lower()
                if os.path.isfile(fpath) and ext in RAGService.EXTENSOES_PERMITIDAS:
                    size_bytes = os.path.getsize(fpath)
                    local_documents.append({
                        "file_name": fname,
                        "extension": ext,
                        "size_kb": round(size_bytes / 1024.0, 2),
                        "estimated_tokens": GraphState.estimate_tokens("x" * size_bytes),
                    })

        return {
            "tools": tools_list,
            "rag": {
                "enabled": bool(tenant.file_search_store_id or local_documents),
                "file_search_store_id": tenant.file_search_store_id or "Não sincronizado / Local",
                "documents_directory": rag_dir.replace("\\", "/"),
                "documents": local_documents,
            },
        }

    async def get_tenant_full_dashboard(self, tenant_id: str, db_limit: int = 50) -> Optional[Dict[str, Any]]:
        """Consolida todos os indicadores de um tenant num único dicionário estruturado."""
        tenant = self.tenant_registry.get_tenant(tenant_id)
        if not tenant:
            return None

        topology = self._inspect_graph_topology(tenant)
        tools_and_rag = self._inspect_tools_and_rag(tenant, topology["nodes"])
        token_metrics = await self.dashboard_repo.get_tenant_token_metrics(
            tenant_id=tenant_id,
            chars_per_token=self.CHARS_PER_TOKEN,
        )
        databases = await self.dashboard_repo.get_tenant_databases_snapshot(
            tenant_id=tenant_id,
            limit=db_limit,
        )

        return {
            "tenant_id": tenant.tenant_id,
            "name": tenant.name,
            "system_instruction_tokens": GraphState.estimate_tokens(tenant.system_instruction),
            "tokens_telemetry": token_metrics,
            "topology": topology,
            "capabilities": tools_and_rag,
            "databases": databases,
        }