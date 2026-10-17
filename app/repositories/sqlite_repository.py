# app/repositories/sqlite_repository.py
import os
import sqlite3
import threading
from typing import Dict, List, Optional
from app.repositories.base import BaseSessionRepository


class SQLiteSessionRepository(BaseSessionRepository):
    """
    Implementação concreta do repositório de sessões utilizando SQLite otimizado para alta concorrência.
    """

    def __init__(self, db_path: str = "data/app_sessions.db"):
        self.db_path = db_path
        self._write_lock = threading.RLock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._create_table()

    def _get_connection(self) -> sqlite3.Connection:
        """
        Abre conexão configurada com WAL e timeout de 30s para suportar acessos simultâneos.
        """
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        # WAL permite múltiplas leituras e escritas concorrentes sem travar o arquivo
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def _create_table(self) -> None:
        """Cria as tabelas de mensagens e de metadados de forma thread-safe."""
        with self._write_lock:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        tenant_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_session_tenant 
                    ON chat_messages(session_id, tenant_id);
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS session_metadata (
                        session_id TEXT NOT NULL,
                        tenant_id TEXT NOT NULL,
                        interaction_id TEXT,
                        PRIMARY KEY (session_id, tenant_id)
                    );
                """)

    def save_message(self, session_id: str, tenant_id: str, role: str, content: str) -> None:
        with self._write_lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO chat_messages (session_id, tenant_id, role, content) 
                    VALUES (?, ?, ?, ?);
                    """,
                    (session_id, tenant_id or "default", role, content),
                )

    def get_history(
        self, session_id: str, tenant_id: Optional[str] = None, limit: int = 10
    ) -> List[Dict[str, str]]:
        # Leituras em modo WAL não bloqueiam outras leituras nem escritas
        with self._get_connection() as conn:
            if tenant_id:
                cursor = conn.execute(
                    "SELECT role, content FROM chat_messages WHERE session_id = ? AND tenant_id = ? ORDER BY id DESC LIMIT ?;",
                    (session_id, tenant_id, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?;",
                    (session_id, limit),
                )
            rows = cursor.fetchall()
            return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def get_last_interaction_id(
        self, session_id: str, tenant_id: Optional[str] = None
    ) -> Optional[str]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT interaction_id FROM session_metadata WHERE session_id = ? AND tenant_id = ?;",
                (session_id, tenant_id or "default"),
            )
            row = cursor.fetchone()
            return row["interaction_id"] if row else None

    def save_interaction_id(
        self, session_id: str, interaction_id: str, tenant_id: Optional[str] = None
    ) -> None:
        with self._write_lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO session_metadata (session_id, tenant_id, interaction_id)
                    VALUES (?, ?, ?)
                    ON CONFLICT(session_id, tenant_id) DO UPDATE SET interaction_id=excluded.interaction_id;
                    """,
                    (session_id, tenant_id or "default", interaction_id),
                )

    def clear_session(self, session_id: str, tenant_id: Optional[str] = None) -> None:
        with self._write_lock:
            with self._get_connection() as conn:
                if tenant_id:
                    conn.execute(
                        "DELETE FROM chat_messages WHERE session_id = ? AND tenant_id = ?;",
                        (session_id, tenant_id),
                    )
                    conn.execute(
                        "DELETE FROM session_metadata WHERE session_id = ? AND tenant_id = ?;",
                        (session_id, tenant_id),
                    )
                else:
                    conn.execute("DELETE FROM chat_messages WHERE session_id = ?;", (session_id,))
                    conn.execute("DELETE FROM session_metadata WHERE session_id = ?;", (session_id,))