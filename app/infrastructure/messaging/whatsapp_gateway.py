# app/infrastructure/messaging/whatsapp_gateway.py
import asyncio
import httpx
from app.core.logger import logger
from app.domain.interfaces import IMessagingGateway


class EvolutionWhatsAppGateway(IMessagingGateway):
    """
    Adaptador assíncrono pronto para produção (Evolution API v2 / Z-API / Meta Cloud).
    """

    def __init__(self, http_client: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self.http_client = http_client
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def send_typing_indicator(self, tenant_id: str, phone_number: str, duration: float) -> None:
        try:
            await self.http_client.post(
                f"{self.base_url}/chat/sendPresence/{tenant_id}",
                headers={"apikey": self.api_key},
                json={"number": phone_number, "presence": "composing", "delay": int(duration * 1000)},
            )
        except Exception as err:
            logger.warning(f"Falha ao enviar presença 'digitando': {err}")
        await asyncio.sleep(duration)

    async def send_text_message(self, tenant_id: str, phone_number: str, text: str) -> None:
        try:
            resp = await self.http_client.post(
                f"{self.base_url}/message/sendText/{tenant_id}",
                headers={"apikey": self.api_key},
                json={"number": phone_number, "text": text},
            )
            resp.raise_for_status()
        except Exception as err:
            logger.error(f"Erro ao enviar mensagem via WhatsApp Gateway: {err}")