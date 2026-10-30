# app/infrastructure/database/aiosqlite_dashboard_repo.py
import asyncio
import math
import os
from typing import Any, Dict, List, Optional
import aiosqlite

from app.core.logger import logger
from app.domain.interfaces import IDashboardRepository
from app.domain.models import GraphState


class AsyncSQLiteDashboardRepository(IDashboardRepository):
    """
    Repositório assíncrono com conexão persistente e escrita em lote (batching)
    para telemetria de tokens e inspeção de bancos por Tenant.
    """

    def __init__(
        self,
        sessions_db_path: str = "data/app_sessions.db",
        tenants_data_dir: str = "data/tenants",
    ) -> None:
        self.sessions_db_path = sessions_db_path
        self.tenants_data_dir = tenants_data_dir
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

            os.makedirs(os.path.dirname(self.sessions_db_path), exist_ok=True)
            conn = await aiosqlite.connect(self.sessions_db_path, timeout=30.0)
            conn.row_factory = aiosqlite.Row

            await conn.execute("PRAGMA journal_mode=WAL;")
            await conn.execute("PRAGMA synchronous=NORMAL;")
            await conn.execute("PRAGMA busy_timeout=30000;")

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS node_telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    node_name TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    input_chars INTEGER NOT NULL DEFAULT 0,
                    output_chars INTEGER NOT NULL DEFAULT 0,
                    elapsed_seconds REAL NOT NULL DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_telemetry_tenant 
                ON node_telemetry(tenant_id, created_at);
            """)
            await conn.commit()
            self._conn = conn

    async def close(self) -> None:
        async with self._init_lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None

    async def save_execution_telemetry(self, state: GraphState, elapsed_seconds: float) -> None:
        """Salva todas as métricas do turno em uma única operação de I/O (executemany)."""
        if not state.debug_metrics:
            return

        db = await self._get_connection()
        rounded_elapsed = round(elapsed_seconds, 3)
        payload = [
            (
                state.tenant_id,
                state.phone_number,
                metric.node_name,
                metric.node_type,
                metric.input_chars,
                metric.output_chars,
                rounded_elapsed,
            )
            for metric in state.debug_metrics
        ]

        await db.executemany(
            """
            INSERT INTO node_telemetry (
                tenant_id, phone_number, node_name, node_type,
                input_chars, output_chars, elapsed_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            payload,
        )
        await db.commit()

    async def get_tenant_token_metrics(self, tenant_id: str, chars_per_token: float = 3.8) -> Dict[str, Any]:
        db = await self._get_connection()

        async with db.execute(
            """
            SELECT 
                role,
                COUNT(*) as total_msgs,
                COALESCE(SUM(LENGTH(content)), 0) as total_chars
            FROM chat_messages
            WHERE tenant_id = ?
            GROUP BY role;
            """,
            (tenant_id,),
        ) as cursor:
            msg_rows = await cursor.fetchall()

        user_chars = 0
        assistant_chars = 0
        user_msgs = 0
        assistant_msgs = 0

        for r in msg_rows:
            if r["role"] == "user":
                user_chars = int(r["total_chars"])
                user_msgs = int(r["total_msgs"])
            elif r["role"] == "assistant":
                assistant_chars = int(r["total_chars"])
                assistant_msgs = int(r["total_msgs"])

        async with db.execute(
            """
            SELECT 
                node_name,
                node_type,
                COUNT(*) as executions,
                COALESCE(SUM(input_chars), 0) as sum_in_chars,
                COALESCE(SUM(output_chars), 0) as sum_out_chars
            FROM node_telemetry
            WHERE tenant_id = ?
            GROUP BY node_name, node_type
            ORDER BY sum_in_chars DESC;
            """,
            (tenant_id,),
        ) as cursor:
            node_rows = await cursor.fetchall()

        breakdown_por_no: List[Dict[str, Any]] = []
        telemetry_in_chars = 0
        telemetry_out_chars = 0

        for row in node_rows:
            in_c = int(row["sum_in_chars"])
            out_c = int(row["sum_out_chars"])
            is_llm = row["node_type"] == "LLM"

            in_tk = math.ceil(in_c / chars_per_token) if is_llm else 0
            out_tk = math.ceil(out_c / chars_per_token) if is_llm else 0

            if is_llm:
                telemetry_in_chars += in_c
                telemetry_out_chars += out_c

            breakdown_por_no.append({
                "node_name": row["node_name"],
                "node_type": row["node_type"],
                "executions": int(row["executions"]),
                "input_chars": in_c,
                "output_chars": out_c,
                "estimated_input_tokens": in_tk,
                "estimated_output_tokens": out_tk,
                "total_tokens": in_tk + out_tk,
            })

        async with db.execute(
            """
            SELECT 
                DATE(created_at) as day,
                SUM(CASE WHEN role = 'user' THEN LENGTH(content) ELSE 0 END) as msg_in_chars,
                SUM(CASE WHEN role = 'assistant' THEN LENGTH(content) ELSE 0 END) as msg_out_chars,
                COUNT(*) as messages_count
            FROM chat_messages
            WHERE tenant_id = ?
            GROUP BY DATE(created_at)
            ORDER BY day ASC
            LIMIT 30;
            """,
            (tenant_id,),
        ) as cursor:
            daily_msg_rows = await cursor.fetchall()

        async with db.execute(
            """
            SELECT 
                DATE(created_at) as day,
                SUM(CASE WHEN node_type = 'LLM' THEN input_chars ELSE 0 END) as tel_in_chars,
                SUM(CASE WHEN node_type = 'LLM' THEN output_chars ELSE 0 END) as tel_out_chars,
                COUNT(*) as node_runs
            FROM node_telemetry
            WHERE tenant_id = ?
            GROUP BY DATE(created_at)
            ORDER BY day ASC
            LIMIT 30;
            """,
            (tenant_id,),
        ) as cursor:
            daily_tel_rows = await cursor.fetchall()

        daily_map: Dict[str, Dict[str, int]] = {}
        for r in daily_msg_rows:
            day_str = str(r["day"] or "Sem Data")
            daily_map[day_str] = {
                "msg_in_chars": int(r["msg_in_chars"] or 0),
                "msg_out_chars": int(r["msg_out_chars"] or 0),
                "tel_in_chars": 0,
                "tel_out_chars": 0,
                "interactions": int(r["messages_count"] or 0),
            }

        for r in daily_tel_rows:
            day_str = str(r["day"] or "Sem Data")
            if day_str not in daily_map:
                daily_map[day_str] = {
                    "msg_in_chars": 0,
                    "msg_out_chars": 0,
                    "tel_in_chars": 0,
                    "tel_out_chars": 0,
                    "interactions": int(r["node_runs"] or 0),
                }
            daily_map[day_str]["tel_in_chars"] = int(r["tel_in_chars"] or 0)
            daily_map[day_str]["tel_out_chars"] = int(r["tel_out_chars"] or 0)

        daily_usage: List[Dict[str, Any]] = []
        for day_str in sorted(daily_map.keys()):
            d_info = daily_map[day_str]
            day_in_chars = max(d_info["tel_in_chars"], d_info["msg_in_chars"])
            day_out_chars = max(d_info["tel_out_chars"], d_info["msg_out_chars"])

            day_in_tokens = math.ceil(day_in_chars / chars_per_token) if day_in_chars > 0 else 0
            day_out_tokens = math.ceil(day_out_chars / chars_per_token) if day_out_chars > 0 else 0

            daily_usage.append({
                "date": day_str,
                "input_chars": day_in_chars,
                "output_chars": day_out_chars,
                "input_tokens": day_in_tokens,
                "output_tokens": day_out_tokens,
                "total_tokens": day_in_tokens + day_out_tokens,
                "interactions": d_info["interactions"],
            })

        effective_in_chars = max(telemetry_in_chars, user_chars)
        effective_out_chars = max(telemetry_out_chars, assistant_chars)

        est_input_tokens = math.ceil(effective_in_chars / chars_per_token) if effective_in_chars > 0 else 0
        est_output_tokens = math.ceil(effective_out_chars / chars_per_token) if effective_out_chars > 0 else 0

        async with db.execute(
            "SELECT COUNT(DISTINCT session_id) as unique_users FROM chat_messages WHERE tenant_id = ?;",
            (tenant_id,),
        ) as cursor:
            unique_row = await cursor.fetchone()
            unique_sessions = int(unique_row["unique_users"]) if unique_row else 0

        return {
            "proxy_rule": f"1 token = {chars_per_token} caracteres",
            "chars_per_token": chars_per_token,
            "unique_sessions": unique_sessions,
            "messages_count": {
                "user": user_msgs,
                "assistant": assistant_msgs,
                "total": user_msgs + assistant_msgs,
            },
            "characters": {
                "input": effective_in_chars,
                "output": effective_out_chars,
                "total": effective_in_chars + effective_out_chars,
            },
            "estimated_tokens": {
                "input": est_input_tokens,
                "output": est_output_tokens,
                "total": est_input_tokens + est_output_tokens,
            },
            "node_breakdown": breakdown_por_no,
            "daily_usage": daily_usage,
        }

    async def get_tenant_databases_snapshot(self, tenant_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        databases_info: List[Dict[str, Any]] = []

        if os.path.exists(self.sessions_db_path):
            session_tables: List[Dict[str, Any]] = []
            db = await self._get_connection()

            for table_name in ("chat_messages", "session_metadata", "node_telemetry"):
                try:
                    async with db.execute(
                        f"SELECT COUNT(*) as total FROM {table_name} WHERE tenant_id = ?;",
                        (tenant_id,),
                    ) as count_cur:
                        c_row = await count_cur.fetchone()
                        total_rows = int(c_row["total"]) if c_row else 0

                    async with db.execute(
                        f"SELECT * FROM {table_name} WHERE tenant_id = ? ORDER BY rowid DESC LIMIT ?;",
                        (tenant_id, limit),
                    ) as rows_cur:
                        rows = [dict(r) for r in await rows_cur.fetchall()]
                        columns = list(rows[0].keys()) if rows else []

                    session_tables.append({
                        "table_name": table_name,
                        "total_records": total_rows,
                        "columns": columns,
                        "rows": rows,
                    })
                except Exception as err:
                    logger.warning(f"[DASHBOARD DB] Ignorando tabela '{table_name}': {err}")

            databases_info.append({
                "database_name": "Banco de Sessões & Telemetria ( Filtrado por Tenant )",
                "db_path": self.sessions_db_path,
                "scope": "SHARED_FILTERED",
                "tables": session_tables,
            })

        tenant_dir = os.path.join(self.tenants_data_dir, tenant_id)
        if os.path.exists(tenant_dir):
            for fname in sorted(os.listdir(tenant_dir)):
                if not fname.lower().endswith((".db", ".sqlite", ".sqlite3")):
                    continue

                db_full_path = os.path.join(tenant_dir, fname)
                dedicated_tables: List[Dict[str, Any]] = []
                try:
                    async with aiosqlite.connect(db_full_path, timeout=30.0) as t_db:
                        t_db.row_factory = aiosqlite.Row
                        async with t_db.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
                        ) as tbl_cur:
                            table_names = [r["name"] for r in await tbl_cur.fetchall()]

                        for t_name in table_names:
                            if not t_name.isidentifier():
                                continue

                            async with t_db.execute(f"SELECT COUNT(*) as total FROM {t_name};") as c_cur:
                                c_row = await c_cur.fetchone()
                                total_records = int(c_row["total"]) if c_row else 0

                            async with t_db.execute(
                                f"SELECT * FROM {t_name} ORDER BY rowid DESC LIMIT ?;", (limit,)
                            ) as r_cur:
                                rows = [dict(r) for r in await r_cur.fetchall()]

                            if not rows:
                                async with t_db.execute(f"PRAGMA table_info({t_name});") as col_cur:
                                    columns = [col["name"] for col in await col_cur.fetchall()]
                            else:
                                columns = list(rows[0].keys())

                            dedicated_tables.append({
                                "table_name": t_name,
                                "total_records": total_records,
                                "columns": columns,
                                "rows": rows,
                            })

                    databases_info.append({
                        "database_name": f"Banco Dedicado ({fname})",
                        "db_path": db_full_path.replace("\\", "/"),
                        "scope": "DEDICATED_TENANT_DB",
                        "tables": dedicated_tables,
                    })
                except Exception as err:
                    logger.error(f"[DASHBOARD DB] Erro ao ler banco {db_full_path}: {err}")

        return databases_info