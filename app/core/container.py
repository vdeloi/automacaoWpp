# app/core/container.py
import asyncio
from typing import Optional
import httpx
from google import genai

from app.core.config import Settings
from app.core.logger import logger
from app.domain.interfaces import (
    IClinicaRepository,
    IDashboardRepository,
    ILLMAgent,
    IMessagingGateway,
    ISessionRepository,
    ITurnPredictor,
)
from app.infrastructure.database.aiosqlite_clinica_repo import AsyncSQLiteClinicaRepository
from app.infrastructure.database.aiosqlite_dashboard_repo import AsyncSQLiteDashboardRepository
from app.infrastructure.database.aiosqlite_session_repo import AsyncSQLiteSessionRepository
from app.infrastructure.llm.gemini_agent import GeminiInteractionsAgent
from app.infrastructure.llm.gemini_predictor import GeminiTurnPredictor
from app.infrastructure.llm.judge_logger import LLMAuditLogger
from app.infrastructure.messaging.mock_gateway import MockMessagingGateway
from app.infrastructure.messaging.whatsapp_gateway import EvolutionWhatsAppGateway
from app.services.chat_orchestrator import ChatOrchestratorService
from app.services.dashboard_service import DashboardService
from app.services.message_buffer import HumanMessageBuffer
from app.services.rag_service import RAGService
from app.tenants.clinica.tenant import ClinicaTenant
from app.tenants.clinica.tools import ClinicaToolService
from app.tenants.padaria.tenant import PadariaTenant
from app.tenants.padaria.tools import PadariaDeliveryToolService
from app.tenants.registry import TenantRegistry


class AppContainer:
    """
    Contêiner IoC (Inversão de Controle) que instancia, injeta e gerencia
    o ciclo de vida de todas as dependências da aplicação.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._rag_sync_task: Optional[asyncio.Task[None]] = None

        # 1. Clientes de Rede e IA Compartilhados
        self.http_client = httpx.AsyncClient(
            headers={"User-Agent": "multi_tenant_assistant_v2"},
            timeout=15.0,
        )
        self.genai_client = genai.Client(api_key=settings.gemini_api_key)

        # 2. Camada de Persistência (Conexões Persistentes)
        self.session_repo: ISessionRepository = AsyncSQLiteSessionRepository(
            db_path=settings.sessions_db_path
        )
        self.clinica_repo: IClinicaRepository = AsyncSQLiteClinicaRepository(
            db_path=settings.clinica_db_path
        )
        self.dashboard_repo: IDashboardRepository = AsyncSQLiteDashboardRepository(
            sessions_db_path=settings.sessions_db_path
        )

        # 3. Definição Centralizada de Modelos (Single Source of Truth)
        fast_model = settings.classifier_model_name or settings.model_name
        self.audit_logger: Optional[LLMAuditLogger] = (
            LLMAuditLogger(
                client=self.genai_client,
                judge_model_name=fast_model,
            )
            if settings.enable_debug_logs
            else None
        )

        # 4. Camada de Inteligência Artificial e Buffer de Turno
        self.turn_predictor: ITurnPredictor = GeminiTurnPredictor(
            client=self.genai_client,
            model_name=fast_model,
        )
        self.message_buffer = HumanMessageBuffer(predictor=self.turn_predictor)
        self.llm_agent: ILLMAgent = GeminiInteractionsAgent(
            client=self.genai_client,
            model_name=settings.model_name,
            audit_callback=self.audit_logger.evaluate_and_log if self.audit_logger else None,
        )

        # 5. Serviços de Ferramentas e Registro de Tenants
        self.padaria_tools = PadariaDeliveryToolService(http_client=self.http_client)
        self.clinica_tools = ClinicaToolService(repository=self.clinica_repo)

        self.rag_service = RAGService(client=self.genai_client)
        self.tenant_registry = TenantRegistry(rag_service=self.rag_service)

        self.tenant_registry.register_tenant(
            PadariaTenant(
                tool_service=self.padaria_tools,
                llm_agent=self.llm_agent,
                message_buffer=self.message_buffer,
            )
        )
        self.tenant_registry.register_tenant(
            ClinicaTenant(
                tool_service=self.clinica_tools,
                llm_agent=self.llm_agent,
                message_buffer=self.message_buffer,
                genai_client=self.genai_client,
                classifier_model_name=fast_model,
            )
        )

        # 6. Camada de Mensageria
        if settings.whatsapp_api_url and settings.whatsapp_api_key:
            self.messaging_gateway: IMessagingGateway = EvolutionWhatsAppGateway(
                http_client=self.http_client,
                base_url=settings.whatsapp_api_url,
                api_key=settings.whatsapp_api_key,
            )
        else:
            self.messaging_gateway = MockMessagingGateway()

        # 7. Motor de Orquestração Baseado em Grafos (FSM)
        self.chat_orchestrator = ChatOrchestratorService(
            tenant_registry=self.tenant_registry,
            session_repo=self.session_repo,
            turn_predictor=self.turn_predictor,
            messaging_gateway=self.messaging_gateway,
            max_iterations=10,
            enable_debug_logs=settings.enable_debug_logs,
            dashboard_repo=self.dashboard_repo,
        )

        # 8. Serviço de Observabilidade e Dashboard Multi-Tenant
        self.dashboard_service = DashboardService(
            tenant_registry=self.tenant_registry,
            dashboard_repo=self.dashboard_repo,
        )

    async def _background_rag_sync(self) -> None:
        """Executa a sincronização de RAG em background sem bloquear o Health Check do FastAPI."""
        try:
            logger.info("[STARTUP] Iniciando sincronização assíncrona de RAG em background...")
            await self.tenant_registry.inicializar_todos_os_rags_async()
            logger.info("[STARTUP] Sincronização de RAG concluída com sucesso.")
        except asyncio.CancelledError:
            logger.warning("[SHUTDOWN] Sincronização de RAG em background cancelada.")
        except Exception as err:
            logger.error(f"[STARTUP] Erro na sincronização de RAG em background: {err}", exc_info=True)

    async def startup(self) -> None:
        """Inicializa conexões de banco de dados imediatamente e dispara o RAG em background."""
        await asyncio.gather(
            self.session_repo.initialize(),
            self.clinica_repo.initialize(),
            self.dashboard_repo.initialize(),
        )
        self._rag_sync_task = asyncio.create_task(self._background_rag_sync())

    async def shutdown(self) -> None:
        """Cancela tarefas em background e encerra conexões de banco e rede de forma limpa."""
        if self._rag_sync_task and not self._rag_sync_task.done():
            self._rag_sync_task.cancel()
            await asyncio.gather(self._rag_sync_task, return_exceptions=True)

        await asyncio.gather(
            self.session_repo.close(),
            self.clinica_repo.close(),
            self.dashboard_repo.close(),
            self.http_client.aclose(),
            return_exceptions=True,
        )