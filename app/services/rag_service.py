# app/services/rag_service.py
import os
import time
from typing import Dict, Optional
from google import genai
from app.core.config import settings

# Definimos quais arquivos o Gemini tem permissão para ler. 
# Arquivos como .db, .jpg ou .mp4 serão completamente ignorados.
EXTENSOES_PERMITIDAS = {".txt", ".pdf", ".md", ".csv"}

class RAGService:
    """
    Gerencia a indexação de documentos e FileSearchStores para cada tenant.
    """
    def __init__(self, client: Optional[genai.Client] = None) -> None:
        self.client = client or genai.Client(api_key=settings.gemini_api_key)
        self._store_cache: Dict[str, str] = {}

    def get_or_create_store(self, tenant_id: str) -> str:
        """Recupera a Store existente ou cria uma nova."""
        if tenant_id in self._store_cache:
            return self._store_cache[tenant_id]

        target_display_name = f"store_{tenant_id}"
        
        # 1. Procura se já existe no Google
        for store in self.client.file_search_stores.list():
            if getattr(store, "display_name", None) == target_display_name:
                self._store_cache[tenant_id] = store.name
                print(f"[RAG] Store existente reaproveitada para '{tenant_id}': {store.name}")
                return store.name

        # 2. Cria se não existir
        new_store = self.client.file_search_stores.create(
            config={
                "display_name": target_display_name,
                "embedding_model": "models/gemini-embedding-2"
            }
        )
        self._store_cache[tenant_id] = new_store.name
        print(f"[RAG] Nova store criada para '{tenant_id}': {new_store.name}")
        return new_store.name

    def upload_tenant_documents(self, tenant_id: str, documents_dir: str) -> Optional[str]:
        """Indexa apenas arquivos permitidos que ainda não estejam na store."""
        try:
            if not os.path.exists(documents_dir):
                return None

            # Monta a lista ignorando subpastas e filtrando pela extensão do arquivo
            arquivos = []
            for f in os.listdir(documents_dir):
                caminho_completo = os.path.join(documents_dir, f)
                
                # Separa o nome do arquivo da extensão (ex: 'clinica', '.db')
                _, extensao = os.path.splitext(f)
                
                # Só adiciona se for um arquivo e se a extensão estiver na lista branca
                if os.path.isfile(caminho_completo) and extensao.lower() in EXTENSOES_PERMITIDAS:
                    arquivos.append(caminho_completo)

            if not arquivos:
                print(f"[AVISO RAG] Nenhum documento de texto válido encontrado em: {documents_dir}")
                return None

            store_name = self.get_or_create_store(tenant_id=tenant_id)

            # Lista documentos já enviados para não duplicar upload
            docs_existentes = [
                getattr(doc, "display_name", None)
                for doc in self.client.file_search_stores.documents.list(parent=store_name)
            ]

            for arquivo_path in arquivos:
                nome_arquivo = os.path.basename(arquivo_path)
                
                if nome_arquivo in docs_existentes:
                    print(f"[RAG] Documento '{nome_arquivo}' já indexado. Pulando upload.")
                    continue

                print(f"[RAG] Enviando e indexando '{nome_arquivo}' na store '{store_name}'...")
                operacao = self.client.file_search_stores.upload_to_file_search_store(
                    file=arquivo_path,
                    file_search_store_name=store_name,
                    config={"display_name": nome_arquivo}
                )

                while not operacao.done:
                    time.sleep(2)
                    operacao = self.client.operations.get(operacao)

                print(f"[RAG] Documento '{nome_arquivo}' indexado com sucesso!")

        except Exception as error:
            print(f"[ERRO RAG] Falha no upload de documentos do tenant {tenant_id}: {error}")
            return None
        else:
            return store_name

rag_service = RAGService()