# app/tenants/clinica/repository.py
import os
import re
import sqlite3
import threading
import unicodedata
from abc import ABC, abstractmethod
from typing import List


class BaseClinicaRepository(ABC):
    """Interface abstrata exclusiva para persistência de dados da Clínica."""

    @abstractmethod
    def check_availability(self, date: str, doctor: str) -> List[str]:
        pass

    @abstractmethod
    def book_appointment(self, client_name: str, date: str, time: str, doctor: str) -> bool:
        pass


class SQLiteClinicaRepository(BaseClinicaRepository):
    """
    Implementação concorrente em SQLite do repositório da Clínica.
    Protegida contra condições de corrida (horário duplo) via WAL + UNIQUE + Normalização.
    """

    def __init__(self, db_path: str = "data/tenants/clinica_01/clinica.db"):
        self.db_path = db_path
        self._write_lock = threading.RLock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._create_table()

    @staticmethod
    def _normalize_doctor(doctor: str) -> str:
        """
        Normaliza o nome do médico (sem acentos, minúsculo e sem prefixo dr/dra)
        para evitar que duas threads agendem o mesmo médico com grafias levemente diferentes.
        """
        texto = unicodedata.normalize("NFKD", doctor).encode("ASCII", "ignore").decode("utf-8")
        texto = texto.lower().strip()
        texto = re.sub(r"^(dr\.?|dra\.?|doutor|doutora)\s+", "", texto)
        return re.sub(r"\s+", " ", texto)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,  # Controle manual de transação para BEGIN IMMEDIATE
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def _create_table(self) -> None:
        with self._write_lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS appointments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        client_name TEXT NOT NULL,
                        appointment_date TEXT NOT NULL,
                        appointment_time TEXT NOT NULL,
                        doctor TEXT NOT NULL,
                        doctor_normalized TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(appointment_date, appointment_time, doctor_normalized)
                    );
                """)
            finally:
                conn.close()

    def check_availability(self, date: str, doctor: str) -> List[str]:
        """Busca no banco os horários já preenchidos de forma concorrente."""
        doc_norm = self._normalize_doctor(doctor)
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT appointment_time FROM appointments 
                WHERE appointment_date = ? AND doctor_normalized = ? 
                ORDER BY appointment_time;
                """,
                (date.strip(), doc_norm),
            )
            rows = cursor.fetchall()
            return [row["appointment_time"] for row in rows]
        finally:
            conn.close()

    def book_appointment(self, client_name: str, date: str, time: str, doctor: str) -> bool:
        """
        Tenta inserir o agendamento com trava transacional imediata (BEGIN IMMEDIATE).
        Se dois pacientes tentarem reservar o mesmo horário juntos, apenas o primeiro
        terá sucesso e o segundo receberá False instantaneamente.
        """
        doc_norm = self._normalize_doctor(doctor)
        with self._write_lock:
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                conn.execute(
                    """
                    INSERT INTO appointments (
                        client_name, appointment_date, appointment_time, doctor, doctor_normalized
                    )
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (client_name.strip(), date.strip(), time.strip(), doctor.strip(), doc_norm),
                )
                conn.execute("COMMIT;")
                return True
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK;")
                return False
            except Exception:
                conn.execute("ROLLBACK;")
                raise
            finally:
                conn.close()