# app/infrastructure/messaging/mock_gateway.py
import asyncio
from app.domain.interfaces import IMessagingGateway


class MockMessagingGateway(IMessagingGateway):
    """Gateway para o Frontend Web Mock: apenas aguarda o tempo de digitação."""

    async def send_typing_indicator(self, tenant_id: str, phone_number: str, duration: float) -> None:
        await asyncio.sleep(duration)

    async def send_text_message(self, tenant_id: str, phone_number: str, text: str) -> None:
        # No Mock HTTP síncrono, a resposta já é devolvida no corpo do JSON do POST.
        pass