# app/main.py
import os
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.dashboard_router import router as dashboard_router
from app.core.config import get_settings
from app.core.container import AppContainer
from app.core.logger import logger
from app.domain.interfaces import IncomingMessage
from app.models.chat import ChatRequest, ChatResponse
from app.services.chat_orchestrator import ChatOrchestratorService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    container = AppContainer(settings=settings)
    app.state.container = container
    logger.info("Inicializando bancos assíncronos e sincronizando RAG...")
    await container.startup()
    yield
    logger.info("Encerrando conexões e pool HTTP...")
    await container.shutdown()


app = FastAPI(
    title="Assistente Virtual Multi-Tenant - GenAI",
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Regista os endpoints REST do Dashboard
app.include_router(dashboard_router)

# Mantém o frontend Mock original intacto e monta a GUI do Dashboard em /dashboard
os.makedirs("dashboard_frontend", exist_ok=True)
app.mount("/mock", StaticFiles(directory="mock_frontend", html=True), name="mock")
app.mount("/dashboard", StaticFiles(directory="dashboard_frontend", html=True), name="dashboard")


def get_orchestrator(request: Request) -> ChatOrchestratorService:
    """Dependency Provider do FastAPI."""
    return request.app.state.container.chat_orchestrator


@app.get("/", tags=["Health Check"])
async def root():
    return RedirectResponse(url="/mock")


@app.post(
    "/api/v1/chat/mock",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    tags=["WhatsApp Mock"],
)
async def process_mock_message(
    request: ChatRequest,
    orchestrator: ChatOrchestratorService = Depends(get_orchestrator),
) -> ChatResponse:
    incoming = IncomingMessage(
        tenant_id=request.tenant_id,
        phone_number=request.phone_number,
        text=request.message,
    )
    return await orchestrator.handle_incoming_message(incoming)