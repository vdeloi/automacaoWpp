# app/main.py
import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Dict, Tuple

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.models.chat import ChatRequest, ChatResponse
from app.services.gemini_agent import GeminiInteractionsAgent
from app.services.message_buffer import LLMTurnPredictor, message_buffer
from app.services.session_manager import session_manager
from app.tenants.registry import tenant_registry

# Locks isolados por (tenant_id, phone_number)
_session_locks: Dict[Tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Configura pool de threads ampliado e inicializa RAG sem bloquear o servidor."""
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=100))

    print("[INICIALIZAÇÃO] Sincronizando RAG dos tenants em background thread...")
    await asyncio.to_thread(tenant_registry.inicializar_todos_os_rags)
    yield
    print("[ENCERRAMENTO] Finalizando aplicação...")


app = FastAPI(
    title="Assistente Virtual Multi-Tenant - GenAI",
    version="1.0.0",
    description="Plataforma multi-tenant para atendimento automatizado via WhatsApp.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/mock", StaticFiles(directory="mock_frontend", html=True), name="mock")

gemini_agent = GeminiInteractionsAgent(
    api_key=settings.gemini_api_key,
    model_name=settings.model_name,
)


@app.get("/", tags=["Health Check"])
async def root():
    """Redireciona a raiz para a interface web."""
    return RedirectResponse(url="/mock")


@app.post(
    "/api/v1/chat/mock",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    tags=["WhatsApp Mock"],
)
async def process_mock_message(request: ChatRequest) -> ChatResponse:
    try:
        tenant = tenant_registry.get_tenant(request.tenant_id)
        if not tenant:
            return ChatResponse(
                response="Desculpe, o estabelecimento selecionado não foi encontrado.",
                interaction_id=None,
                status="error",
            )

        # 1. Buffer Inteligente: Micro-debounce (1.5s) + Previsão de Fim de Turno via LLM Leve
        mensagem_unificada = await message_buffer.enqueue_and_wait(
            tenant_id=request.tenant_id,
            phone_number=request.phone_number,
            message=request.message,
        )

        # Se retornou None, o usuário enviou outra mensagem antes do turno acabar.
        # Encerramos esta requisição intermediária; a última requisição responderá tudo junto.
        if mensagem_unificada is None:
            return ChatResponse(
                response="",
                interaction_id=None,
                status="buffered",
            )

        # 2. Processamento no Agente Principal com as mensagens unificadas
        session_key = (request.tenant_id, request.phone_number)
        async with _session_locks[session_key]:
            previous_id = await asyncio.to_thread(
                session_manager.get_last_interaction_id,
                tenant_id=request.tenant_id,
                phone_number=request.phone_number,
            )

            await asyncio.to_thread(
                session_manager.add_message,
                phone_number=request.phone_number,
                tenant_id=request.tenant_id,
                role="user",
                content=mensagem_unificada,
            )

            agent_response = await gemini_agent.generate_interaction(
                user_input=mensagem_unificada,
                system_instruction=tenant.system_instruction,
                previous_interaction_id=previous_id,
                tool_schemas=tenant.get_all_tool_schemas(),
                tool_map=tenant.get_tool_map(),
            )

            if agent_response.interaction_id:
                await asyncio.to_thread(
                    session_manager.save_interaction_id,
                    tenant_id=request.tenant_id,
                    phone_number=request.phone_number,
                    interaction_id=agent_response.interaction_id,
                )

            await asyncio.to_thread(
                session_manager.add_message,
                phone_number=request.phone_number,
                tenant_id=request.tenant_id,
                role="assistant",
                content=agent_response.text_output,
            )

        # 3. Simula o tempo de digitação proporcional ao tamanho da resposta gerada
        typing_delay = LLMTurnPredictor.calculate_typing_delay(agent_response.text_output)
        await asyncio.sleep(typing_delay)

    except Exception as error:
        print(f"[ERRO NO PROCESSAMENTO] Falha no endpoint: {error}")
        return ChatResponse(
            response="Ocorreu um erro temporário ao processar o seu atendimento.",
            interaction_id=None,
            status="error",
        )
    else:
        return ChatResponse(
            response=agent_response.text_output,
            interaction_id=agent_response.interaction_id,
            status="success",
        )