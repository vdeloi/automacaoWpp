# app/tenants/registry.py
import asyncio
import os
from typing import Dict, List, Optional
from app.core.logger import logger
from app.services.rag_service import RAGService
from app.tenants.base import BaseTenant


class TenantRegistry:
    """Catálogo central de empresas com inicialização assíncrona paralela de RAG."""

    def __init__(self, rag_service: Optional[RAGService] = None) -> None:
        self._tenants: Dict[str, BaseTenant] = {}
        self._rag_service = rag_service

    def register_tenant(self, tenant: BaseTenant) -> None:
        self._tenants[tenant.tenant_id] = tenant
        logger.info(f"[REGISTRY] Tenant registrado: {tenant.name} ({tenant.tenant_id})")

    def get_tenant(self, tenant_id: str) -> Optional[BaseTenant]:
        tenant = self._tenants.get(tenant_id)
        if not tenant:
            logger.warning(f"[REGISTRY] Tenant '{tenant_id}' não encontrado.")
        return tenant

    def list_tenants(self) -> List[BaseTenant]:
        """Retorna a lista de todos os tenants registados na plataforma."""
        return list(self._tenants.values())

    async def _init_single_tenant_rag(self, tenant_id: str, tenant: BaseTenant) -> None:
        if not self._rag_service:
            return
        caminho_documentos = os.path.join("data", "tenants", tenant_id)
        if os.path.exists(caminho_documentos):
            store_name = await self._rag_service.upload_tenant_documents_async(
                tenant_id=tenant_id,
                documents_dir=caminho_documentos,
            )
            if store_name:
                tenant.file_search_store_id = store_name

    async def inicializar_todos_os_rags_async(self) -> None:
        if not self._rag_service:
            return
        await asyncio.gather(
            *(self._init_single_tenant_rag(t_id, tenant) for t_id, tenant in self._tenants.items())
        )