# app/tenants/base.py
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple
from app.domain.interfaces import AppTool, IGraphNode


class BaseTenant(ABC):
    """Contrato base para qualquer empresa (Tenant) cadastrada na plataforma."""

    def __init__(self) -> None:
        self._file_search_store_id: Optional[str] = None

    @property
    @abstractmethod
    def tenant_id(self) -> str:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def system_instruction(self) -> str:
        pass

    @property
    def file_search_store_id(self) -> Optional[str]:
        return self._file_search_store_id

    @file_search_store_id.setter
    def file_search_store_id(self, value: Optional[str]) -> None:
        self._file_search_store_id = value

    @abstractmethod
    def get_tools(self) -> List[AppTool]:
        pass

    def get_tool_map(self) -> Dict[str, AppTool]:
        return {tool.name: tool for tool in self.get_tools()}

    @abstractmethod
    def build_graph(self) -> Tuple[Dict[str, IGraphNode], str]:
        """
        Fábrica do Grafo de Atendimento do Tenant.
        Retorna (dicionario_de_nos, nome_do_no_inicial).
        """
        pass