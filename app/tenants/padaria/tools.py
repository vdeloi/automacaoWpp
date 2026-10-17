# app/tenants/padaria/tools.py
import math
import re
import threading
import time
from functools import lru_cache
from typing import Optional, Tuple

from geopy.distance import geodesic
from geopy.geocoders import Nominatim
from app.tools.base_tool import AppTool

# Coordenadas fixas da Padaria Pão Quente (Rua General Osório, 500, Centro, Ribeirão Preto - SP)
# Evita fazer chamada de rede repetida para o mesmo endereço a cada requisição concorrente.
COORDENADAS_PADARIA: Tuple[float, float] = (-21.177631, -47.810098)

# Lock e timestamp para respeitar o Rate Limit de 1 req/s da API Nominatim sob alta concorrência
_nominatim_lock = threading.Lock()
_last_nominatim_call: float = 0.0
_geolocator = Nominatim(user_agent="padaria_pao_quente_wpp_v3")


def _geocode_rate_limited(query: str) -> Optional[Tuple[float, float]]:
    """
    Executa chamada ao Nominatim protegida por Lock para que múltiplas threads
    não causem bloqueio de IP (HTTP 429/403) por excesso de requisições simultâneas.
    """
    global _last_nominatim_call
    with _nominatim_lock:
        elapsed = time.monotonic() - _last_nominatim_call
        if elapsed < 1.1:
            time.sleep(1.1 - elapsed)
        loc = _geolocator.geocode(query, timeout=10)
        _last_nominatim_call = time.monotonic()
        if loc:
            return (loc.latitude, loc.longitude)
        return None


def calcular_valor_por_distancia(distancia_km: float) -> Tuple[float, bool]:
    """Calcula a taxa de entrega com base na distância linear em quilômetros."""
    if distancia_km < 0:
        return 0.0, False
    if distancia_km <= 0.5:
        return 0.0, True
    elif distancia_km <= 1.0:
        return 7.0, True
    elif distancia_km <= 2.0:
        return 8.0, True
    elif distancia_km <= 3.0:
        return 9.0, True
    elif distancia_km <= 10.0:
        km_adicional = math.ceil(distancia_km - 3.0)
        valor = 9.0 + (km_adicional * 1.0)
        return valor, True
    else:
        return 0.0, False


def _limpar_formatacao_endereco(texto: str) -> str:
    """Remove vírgulas repetidas, espaços extras e normaliza o estado."""
    texto = re.sub(r"\bSP\b", "São Paulo", texto, flags=re.IGNORECASE)
    texto = re.sub(r"[,\s]*,[,\s]*", ", ", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" ,")
    return texto


@lru_cache(maxsize=512)
def geocodificar_com_fallback(endereco: str) -> Optional[Tuple[float, float]]:
    """
    Estratégia de geocodificação progressiva com cache em memória (Thread-Safe).
    Se dois clientes consultarem o mesmo endereço/rua, o retorno é imediato (0ms).
    """
    endereco_limpo = _limpar_formatacao_endereco(endereco)

    # 1. Tentativa 1: Endereço completo formatado
    coords = _geocode_rate_limited(f"{endereco_limpo}, Brasil")
    if coords:
        return coords

    # 2. Tentativa 2: Sem números prediais
    sem_numero = re.sub(r"\b\d+\b", "", endereco_limpo)
    sem_numero = _limpar_formatacao_endereco(sem_numero)
    coords = _geocode_rate_limited(f"{sem_numero}, Brasil")
    if coords:
        return coords

    # 3. Tentativa 3: Sem prefixos de logradouro
    sem_prefixo = re.sub(
        r"\b(rua|avenida|av|travessa|alameda|r\.)\b", "", sem_numero, flags=re.IGNORECASE
    )
    sem_prefixo = _limpar_formatacao_endereco(sem_prefixo)
    coords = _geocode_rate_limited(f"{sem_prefixo}, Brasil")
    if coords:
        return coords

    # 4. Tentativa 4: Variações fonéticas/ortográficas comuns
    if "giacheto" in sem_prefixo.lower():
        variacao = re.sub(r"giacheto", "Giachetto", sem_prefixo, flags=re.IGNORECASE)
        coords = _geocode_rate_limited(f"{variacao}, Brasil")
        if coords:
            return coords

    return None


def calcular_taxa_entrega_handler(endereco_cliente: str) -> str:
    """Função handler executada em thread separada pelo agente Gemini."""
    print(f"[TOOL EXECUTADA - PADARIA] Calculando entrega para: {endereco_cliente}")
    try:
        ponto_cliente = geocodificar_com_fallback(endereco_cliente)

        if not ponto_cliente:
            return (
                f"Não foi possível localizar o endereço '{endereco_cliente}'. "
                "Por favor, peça ao cliente para confirmar o nome da rua, bairro ou CEP."
            )

        distancia_km = geodesic(COORDENADAS_PADARIA, ponto_cliente).kilometers
        valor, entrega_disponivel = calcular_valor_por_distancia(distancia_km)

        if not entrega_disponivel:
            return (
                f"A distância calculada até o endereço é de aproximadamente {distancia_km:.2f} km. "
                "Infelizmente este endereço ultrapassa o nosso limite de atendimento de 10 km."
            )

        if valor == 0.0:
            return (
                f"A distância calculada é de {distancia_km:.2f} km. "
                "A taxa de entrega é GRÁTIS para este endereço!"
            )

        return (
            f"A distância calculada até o local é de aproximadamente {distancia_km:.2f} km. "
            f"A taxa de entrega fica em R$ {valor:.2f}."
        )

    except Exception as error:
        print(f"[ERRO GEOPY] Falha ao calcular rota: {error}")
        return "Ocorreu uma instabilidade temporária no serviço de mapas ao consultar o endereço."


tool_calcular_taxa_entrega = AppTool(
    name="calcular_taxa_entrega",
    description="Calcula o valor da taxa de entrega para a Padaria Pão Quente com base no endereço do cliente.",
    parameters={
        "type": "object",
        "properties": {
            "endereco_cliente": {
                "type": "string",
                "description": "Endereço de destino fornecido pelo cliente.",
            }
        },
        "required": ["endereco_cliente"],
    },
    handler=calcular_taxa_entrega_handler,
)


def verificar_fornada_handler() -> str:
    return "A próxima fornada de pão francês sai daqui a 10 minutos!"


tool_verificar_fornada = AppTool(
    name="verificar_status_fornada",
    description="Verifica a previsão do horário da próxima fornada de pão quente.",
    parameters={"type": "object", "properties": {}},
    handler=verificar_fornada_handler,
)