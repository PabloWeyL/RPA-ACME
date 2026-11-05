"""Conexion SQLite transaccional sin esquemas de negocio."""
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sqlite3


def ahora():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ConexionSQLite:
    def __init__(self, ruta):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def conexion(self):
        con = sqlite3.connect(self.ruta)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            with con:
                yield con
        finally:
            con.close()

