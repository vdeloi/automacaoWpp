# app/tenants/clinica/tools.py
from typing import List
from app.core.logger import logger
from app.domain.interfaces import AppTool, IClinicaRepository


class ClinicaToolService:
    """Ferramentas da clínica desacopladas do banco de dados concreto via Injeção de Dependência."""

    def __init__(self, repository: IClinicaRepository) -> None:
        self.repository = repository

    async def consultar_horarios(self, data: str, medico: str) -> str:
        logger.info(f"Consultando disponibilidade: {data} com {medico}")
        ocupados = await self.repository.check_availability(data, medico)
        if not ocupados:
            return f"A agenda do(a) {medico} está totalmente livre no dia {data}. Ofereça qualquer horário comercial."
        return f"No dia {data}, o(a) {medico} já tem consultas nos horários: {', '.join(ocupados)}. Os demais estão livres."

    async def registrar_agendamento(self, nome_paciente: str, data: str, horario: str, medico: str) -> str:
        logger.info(f"Agendando {nome_paciente} em {data} às {horario} com {medico}")
        sucesso = await self.repository.book_appointment(nome_paciente, data, horario, medico)
        if sucesso:
            return f"Sucesso! A consulta de {nome_paciente} foi agendada para {data} às {horario} com {medico}."
        return f"Erro: O horário das {horario} no dia {data} com {medico} já está ocupado. Peça para escolher outro."

    def get_tools(self) -> List[AppTool]:
        return [
            AppTool(
                name="consultar_horarios_disponiveis",
                description="Consulta os horários ocupados e livres em uma data específica com um profissional.",
                parameters={
                    "type": "object",
                    "properties": {
                        "data": {"type": "string", "description": "Data no formato DD/MM/AAAA."},
                        "medico": {"type": "string", "description": "Nome do médico ou dentista."},
                    },
                    "required": ["data", "medico"],
                },
                handler=self.consultar_horarios,
            ),
            AppTool(
                name="registrar_agendamento",
                description="Registra um agendamento de consulta médica/odontológica de forma definitiva.",
                parameters={
                    "type": "object",
                    "properties": {
                        "nome_paciente": {"type": "string", "description": "Nome completo do paciente."},
                        "data": {"type": "string", "description": "Data no formato DD/MM/AAAA."},
                        "horario": {"type": "string", "description": "Horário desejado (ex: '14:00')."},
                        "medico": {"type": "string", "description": "Nome do profissional."},
                    },
                    "required": ["nome_paciente", "data", "horario", "medico"],
                },
                handler=self.registrar_agendamento,
            ),
        ]