# app/services/rag_service.py
import asyncio
import os
from typing import Dict, Optional, Set
from google import genai
from app.core.logger import logger


class RAGService:
    """Gerencia FileSearchStores de cada tenant de forma assíncrona e desacoplada."""

    EXTENSOES_PERMITIDAS: Set[str] = {".txt", ".pdf", ".md", ".csv"}

    def __init__(self, client: genai.Client) -> None:
        self.client = client
        self._store_cache: Dict[str, str] = {}

    async def get_or_create_store_async(self, tenant_id: str) -> str:
        if tenant_id in self._store_cache:
            return self._store_cache[tenant_id]

        target_display_name = f"store_{tenant_id}"

        # Lista as stores existentes usando a API assíncrona
        stores_pager = await self.client.aio.file_search_stores.list()
        async for store in stores_pager:
            if getattr(store, "display_name", None) == target_display_name:
                self._store_cache[tenant_id] = store.name
                logger.info(f"[RAG] Store reaproveitada para '{tenant_id}': {store.name}")
                return store.name

        new_store = await self.client.aio.file_search_stores.create(
            config={
                "display_name": target_display_name,
                "embedding_model": "models/gemini-embedding-2",
            }
        )
        self._store_cache[tenant_id] = new_store.name
        logger.info(f"[RAG] Nova store criada para '{tenant_id}': {new_store.name}")
        return new_store.name

    async def upload_tenant_documents_async(self, tenant_id: str, documents_dir: str) -> Optional[str]:
        try:
            if not os.path.exists(documents_dir):
                return None

            arquivos = [
                os.path.join(documents_dir, f)
                for f in os.listdir(documents_dir)
                if os.path.isfile(os.path.join(documents_dir, f))
                and os.path.splitext(f)[1].lower() in self.EXTENSOES_PERMITIDAS
            ]

            if not arquivos:
                logger.warning(f"[RAG] Nenhum documento válido em: {documents_dir}")
                return None

            store_name = await self.get_or_create_store_async(tenant_id=tenant_id)

            docs_existentes = set()
            docs_pager = await self.client.aio.file_search_stores.documents.list(parent=store_name)
            async for doc in docs_pager:
                nome = getattr(doc, "display_name", None)
                if nome:
                    docs_existentes.add(nome)

            for arquivo_path in arquivos:
                nome_arquivo = os.path.basename(arquivo_path)
                if nome_arquivo in docs_existentes:
                    logger.info(f"[RAG] '{nome_arquivo}' já indexado para {tenant_id}. Pulando.")
                    continue

                logger.info(f"[RAG] Indexando '{nome_arquivo}' na store '{store_name}'...")
                operacao = await self.client.aio.file_search_stores.upload_to_file_search_store(
                    file=arquivo_path,
                    file_search_store_name=store_name,
                    config={"display_name": nome_arquivo},
                )

                while not operacao.done:
                    await asyncio.sleep(2.0)
                    operacao = await self.client.aio.operations.get(operacao)

                logger.info(f"[RAG] '{nome_arquivo}' indexado com sucesso!")

            return store_name
        except Exception as err:
            logger.error(f"[RAG] Falha no upload de documentos do tenant {tenant_id}: {err}")
            return None