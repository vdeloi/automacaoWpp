# app/tenants/padaria/tools.py
import asyncio
import math
import re
import time
from typing import Dict, List, Optional, Tuple
import httpx
from geopy.distance import geodesic
from app.core.logger import logger
from app.domain.interfaces import AppTool


class PadariaDeliveryToolService:
    """Serviço encapsulado e 100% assíncrono para cálculo de entrega."""

    COORDENADAS_PADARIA: Tuple[float, float] = (-21.177631, -47.810098)

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None) -> None:
        self._http_client = http_client
        self._rate_limit_lock = asyncio.Lock()
        self._last_call: float = 0.0
        self._cache: Dict[str, Optional[Tuple[float, float]]] = {}

    async def _geocode_async(self, query: str) -> Optional[Tuple[float, float]]:
        """Respeita o rate limit de 1 req/s da API Nominatim sem bloquear threads."""
        async with self._rate_limit_lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < 1.1:
                await asyncio.sleep(1.1 - elapsed)

            client = self._http_client or httpx.AsyncClient(
                headers={"User-Agent": "padaria_pao_quente_wpp_v3"}, timeout=10.0
            )
            try:
                resp = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": query, "format": "json", "limit": 1},
                )
                self._last_call = time.monotonic()
                data = resp.json()
                if data:
                    return (float(data[0]["lat"]), float(data[0]["lon"]))
                return None
            finally:
                if not self._http_client:
                    await client.aclose()

    @staticmethod
    def _limpar_formatacao_endereco(texto: str) -> str:
        texto = re.sub(r"\bSP\b", "São Paulo", texto, flags=re.IGNORECASE)
        texto = re.sub(r"[,\s]*,[,\s]*", ", ", texto)
        return re.sub(r"\s+", " ", texto).strip(" ,")

    async def geocodificar_com_fallback(self, endereco: str) -> Optional[Tuple[float, float]]:
        endereco_limpo = self._limpar_formatacao_endereco(endereco)
        if endereco_limpo in self._cache:
            return self._cache[endereco_limpo]

        # Tentativa 1: Completo
        coords = await self._geocode_async(f"{endereco_limpo}, Brasil")
        if not coords:
            # Tentativa 2: Sem números
            sem_numero = self._limpar_formatacao_endereco(re.sub(r"\b\d+\b", "", endereco_limpo))
            coords = await self._geocode_async(f"{sem_numero}, Brasil")

        if not coords:
            # Tentativa 3: Sem prefixos
            sem_prefixo = self._limpar_formatacao_endereco(
                re.sub(r"\b(rua|avenida|av|travessa|alameda|r\.)\b", "", sem_numero, flags=re.IGNORECASE)
            )
            coords = await self._geocode_async(f"{sem_prefixo}, Brasil")

        self._cache[endereco_limpo] = coords
        return coords

    @staticmethod
    def calcular_valor_por_distancia(distancia_km: float) -> Tuple[float, bool]:
        if distancia_km < 0 or distancia_km > 10.0:
            return 0.0, False
        if distancia_km <= 0.5:
            return 0.0, True
        if distancia_km <= 1.0:
            return 7.0, True
        if distancia_km <= 2.0:
            return 8.0, True
        if distancia_km <= 3.0:
            return 9.0, True
        return 9.0 + (math.ceil(distancia_km - 3.0) * 1.0), True

    async def calcular_taxa_entrega(self, endereco_cliente: str) -> str:
        logger.info(f"Calculando entrega para: {endereco_cliente}")
        try:
            ponto_cliente = await self.geocodificar_com_fallback(endereco_cliente)
            if not ponto_cliente:
                return (
                    f"Não foi possível localizar o endereço '{endereco_cliente}'. "
                    "Por favor, peça ao cliente para confirmar o nome da rua, bairro ou CEP."
                )

            distancia_km = geodesic(self.COORDENADAS_PADARIA, ponto_cliente).kilometers
            valor, disponivel = self.calcular_valor_por_distancia(distancia_km)

            if not disponivel:
                return (
                    f"A distância calculada é de {distancia_km:.2f} km. "
                    "Infelizmente ultrapassa nosso limite de 10 km."
                )
            if valor == 0.0:
                return f"A distância é de {distancia_km:.2f} km. A taxa de entrega é GRÁTIS!"
            return f"A distância é de aproximadamente {distancia_km:.2f} km. A taxa fica em R$ {valor:.2f}."
        except Exception as err:
            logger.error(f"Erro ao calcular rota: {err}")
            return "Ocorreu uma instabilidade temporária no serviço de mapas."

    async def verificar_fornada(self) -> str:
        return "A próxima fornada de pão francês sai daqui a 10 minutos!"

    def get_tools(self) -> List[AppTool]:
        return [
            AppTool(
                name="calcular_taxa_entrega",
                description="Calcula o valor da taxa de entrega com base no endereço do cliente.",
                parameters={
                    "type": "object",
                    "properties": {
                        "endereco_cliente": {"type": "string", "description": "Endereço completo do cliente."}
                    },
                    "required": ["endereco_cliente"],
                },
                handler=self.calcular_taxa_entrega,
            ),
            AppTool(
                name="verificar_status_fornada",
                description="Verifica a previsão do horário da próxima fornada de pão quente.",
                parameters={"type": "object", "properties": {}},
                handler=self.verificar_fornada,
            ),
        ]