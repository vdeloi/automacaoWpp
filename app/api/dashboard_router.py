# app/api/dashboard_router.py
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.services.dashboard_service import DashboardService

router = APIRouter(prefix="/api/v1/dashboard", tags=["Tenant Dashboard & Insights"])


def get_dashboard_service(request: Request) -> DashboardService:
    """Dependency Provider do FastAPI para injetar o DashboardService do AppContainer."""
    return request.app.state.container.dashboard_service


@router.get("/tenants", status_code=status.HTTP_200_OK)
async def list_registered_tenants(
    service: DashboardService = Depends(get_dashboard_service),
) -> List[Dict[str, Any]]:
    """Retorna a lista de todos os tenants disponíveis para seleção na GUI."""
    return service.list_tenants_summary()


@router.get("/tenants/{tenant_id}", status_code=status.HTTP_200_OK)
async def get_tenant_dashboard_details(
    tenant_id: str,
    db_limit: int = Query(default=50, ge=5, le=200, description="Limite de linhas por tabela SQL."),
    service: DashboardService = Depends(get_dashboard_service),
) -> Dict[str, Any]:
    """
    Retorna os dados consolidados do tenant:
    - Consumo de tokens (proxy de 3.8 caracteres/token)
    - Topologia completa do fluxo de agentes (nós, arestas e Mermaid.js)
    - Ferramentas (AppTools) e documentos RAG
    - Visualização das tabelas de banco de dados do tenant
    """
    data = await service.get_tenant_full_dashboard(tenant_id=tenant_id, db_limit=db_limit)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant '{tenant_id}' não encontrado no registo.",
        )
    return data