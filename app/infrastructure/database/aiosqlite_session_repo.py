# app/infrastructure/database/aiosqlite_session_repo.py
import asyncio
import os
from typing import Dict, List, Optional
import aiosqlite

from app.domain.interfaces import ISessionRepository


class AsyncSQLiteSessionRepository(ISessionRepository):
    """Repositório de sessão não-bloqueante com conexão persistente para alta concorrência."""

    def __init__(self, db_path: str = "data/app_sessions.db") -> None:
        self.db_path = db_path
        self._init_lock = asyncio.Lock()
        self._conn: Optional[aiosqlite.Connection] = None

    async def _get_connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            await self.initialize()
        assert self._conn is not None
        return self._conn

    async def initialize(self) -> None:
        async with self._init_lock:
            if self._conn is not None:
                return

            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            conn = await aiosqlite.connect(self.db_path, timeout=30.0)
            conn.row_factory = aiosqlite.Row

            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute("PRAGMA busy_timeout=30000;")

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_tenant 
                ON chat_messages(session_id, tenant_id);
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS session_metadata (
                    session_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    interaction_id TEXT,
                    PRIMARY KEY (session_id, tenant_id)
                );
            """)
            await conn.commit()
            self._conn = conn

    async def close(self) -> None:
        async with self._init_lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None

    async def save_message(self, session_id: str, tenant_id: str, role: str, content: str) -> None:
        db = await self._get_connection()
        await db.execute(
            "INSERT INTO chat_messages (session_id, tenant_id, role, content) VALUES (?, ?, ?, ?);",
            (session_id, tenant_id, role, content),
        )
        await db.commit()

    async def get_history(self, session_id: str, tenant_id: str, limit: int = 10) -> List[Dict[str, str]]:
        db = await self._get_connection()
        async with db.execute(
            "SELECT role, content FROM chat_messages WHERE session_id = ? AND tenant_id = ? ORDER BY id DESC LIMIT ?;",
            (session_id, tenant_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    async def get_last_interaction_id(self, session_id: str, tenant_id: str) -> Optional[str]:
        db = await self._get_connection()
        async with db.execute(
            "SELECT interaction_id FROM session_metadata WHERE session_id = ? AND tenant_id = ?;",
            (session_id, tenant_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row["interaction_id"] if row else None

    async def save_interaction_id(self, session_id: str, tenant_id: str, interaction_id: str) -> None:
        db = await self._get_connection()
        await db.execute(
            """
            INSERT INTO session_metadata (session_id, tenant_id, interaction_id)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id, tenant_id) DO UPDATE SET interaction_id=excluded.interaction_id;
            """,
            (session_id, tenant_id, interaction_id),
        )
        await db.commit()

    async def clear_session(self, session_id: str, tenant_id: str) -> None:
        db = await self._get_connection()
        await db.execute("DELETE FROM chat_messages WHERE session_id = ? AND tenant_id = ?;", (session_id, tenant_id))
        await db.execute("DELETE FROM session_metadata WHERE session_id = ? AND tenant_id = ?;", (session_id, tenant_id))
        await db.commit()