# app/infrastructure/database/aiosqlite_clinica_repo.py
import asyncio
import os
import re
import unicodedata
from typing import List, Optional
import aiosqlite

from app.core.logger import logger
from app.domain.interfaces import IClinicaRepository


class AsyncSQLiteClinicaRepository(IClinicaRepository):
    """
    Repositório SQLite assíncrono com conexão persistente para agendamentos da clínica.
    Delega o controle de concorrência e atomicidade para o motor WAL + UNIQUE do SQLite.
    """

    def __init__(self, db_path: str = "data/tenants/clinica_01/clinica.db") -> None:
        self.db_path = db_path
        self._init_lock = asyncio.Lock()
        self._conn: Optional[aiosqlite.Connection] = None

    @staticmethod
    def _normalize_doctor(doctor: str) -> str:
        texto = unicodedata.normalize("NFKD", doctor).encode("ASCII", "ignore").decode("utf-8")
        texto = texto.lower().strip()
        texto = re.sub(r"^(dr\.?|dra\.?|doutor|doutora)\s+", "", texto)
        return re.sub(r"\s+", " ", texto)

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
                CREATE TABLE IF NOT EXISTS appointments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_name TEXT NOT NULL,
                    appointment_date TEXT NOT NULL,
                    appointment_time TEXT NOT NULL,
                    doctor TEXT NOT NULL,
                    doctor_normalized TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(appointment_date, appointment_time, doctor_normalized)
                );
            """)
            await conn.commit()

            async with conn.execute("PRAGMA table_info(appointments);") as cursor:
                colunas = [row["name"] async for row in cursor]

            if "doctor_normalized" not in colunas:
                logger.warning("[DB MIGRATION] Adicionando coluna 'doctor_normalized' ausente...")
                await conn.execute(
                    "ALTER TABLE appointments ADD COLUMN doctor_normalized TEXT NOT NULL DEFAULT '';"
                )
                await conn.commit()

                async with conn.execute("SELECT id, doctor FROM appointments;") as select_cursor:
                    registos = await select_cursor.fetchall()
                    for row in registos:
                        doc_norm = self._normalize_doctor(row["doctor"])
                        await conn.execute(
                            "UPDATE appointments SET doctor_normalized = ? WHERE id = ?;",
                            (doc_norm, row["id"]),
                        )
                await conn.commit()
                logger.info("[DB MIGRATION] Migração de 'doctor_normalized' concluída com sucesso.")

            self._conn = conn

    async def close(self) -> None:
        async with self._init_lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None

    async def check_availability(self, date: str, doctor: str) -> List[str]:
        doc_norm = self._normalize_doctor(doctor)
        db = await self._get_connection()
        async with db.execute(
            """
            SELECT appointment_time FROM appointments 
            WHERE appointment_date = ? AND doctor_normalized = ? 
            ORDER BY appointment_time;
            """,
            (date.strip(), doc_norm),
        ) as cursor:
            rows = await cursor.fetchall()
            return [row["appointment_time"] for row in rows]

    async def book_appointment(self, client_name: str, date: str, time: str, doctor: str) -> bool:
        """
        Executa inserção atômica apoiada pela constraint UNIQUE(appointment_date, appointment_time, doctor_normalized).
        """
        doc_norm = self._normalize_doctor(doctor)
        db = await self._get_connection()
        try:
            await db.execute(
                """
                INSERT INTO appointments (
                    client_name, appointment_date, appointment_time, doctor, doctor_normalized
                ) VALUES (?, ?, ?, ?, ?);
                """,
                (client_name.strip(), date.strip(), time.strip(), doctor.strip(), doc_norm),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False