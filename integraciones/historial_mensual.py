"""Persistencia exclusiva de solicitudes y ejecuciones mensuales."""
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from automatizacion.sqlite import ConexionSQLite, ahora


class HistorialMensual:
    def __init__(self, ruta):
        self.base = ConexionSQLite(ruta)
        with self.base.conexion() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS ejecuciones_mensuales (
                    id TEXT PRIMARY KEY, inicio TEXT NOT NULL, fin TEXT,
                    estado TEXT NOT NULL, archivo TEXT NOT NULL, limite INTEGER,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS descargas_mensuales (
                    id INTEGER PRIMARY KEY,
                    ejecucion_id TEXT NOT NULL REFERENCES ejecuciones_mensuales(id),
                    fila INTEGER NOT NULL, proveedor_taxid TEXT NOT NULL,
                    anio INTEGER NOT NULL, mes INTEGER NOT NULL,
                    estado TEXT NOT NULL DEFAULT 'pendiente'
                        CHECK(estado IN ('pendiente','descargado','sin_reporte','error')),
                    intentos INTEGER NOT NULL DEFAULT 0, archivo TEXT, error TEXT,
                    captura TEXT, fecha_fin TEXT,
                    UNIQUE(ejecucion_id, proveedor_taxid, anio, mes)
                );
            """)
            columnas = {fila['name'] for fila in con.execute('PRAGMA table_info(ejecuciones_mensuales)')}
            if 'reporte_pendiente' not in columnas:
                con.execute('ALTER TABLE ejecuciones_mensuales ADD COLUMN reporte_pendiente INTEGER NOT NULL DEFAULT 0')
                con.execute("UPDATE ejecuciones_mensuales SET reporte_pendiente=1 WHERE estado='en_proceso'")

    def iniciar(self, archivo, limite):
        identificador = datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid4().hex[:8]
        with self.base.conexion() as con:
            con.execute("INSERT INTO ejecuciones_mensuales(id,inicio,estado,archivo,limite,reporte_pendiente) VALUES(?,?,'en_proceso',?,?,1)",
                        (identificador, ahora(), str(Path(archivo).resolve()), limite))
        return identificador

    def encolar(self, ejecucion, solicitudes):
        with self.base.conexion() as con:
            con.executemany('INSERT INTO descargas_mensuales(ejecucion_id,fila,proveedor_taxid,anio,mes) VALUES(?,?,?,?,?)',
                            [(ejecucion, s.fila, s.proveedor_taxid, s.anio, s.mes) for s in solicitudes])

    def tareas(self, ejecucion):
        with self.base.conexion() as con:
            return [dict(fila) for fila in con.execute('SELECT * FROM descargas_mensuales WHERE ejecucion_id=? ORDER BY id', (ejecucion,))]

    def intento(self, identificador, numero):
        with self.base.conexion() as con:
            con.execute('UPDATE descargas_mensuales SET intentos=? WHERE id=?', (numero, identificador))

    def resolver(self, identificador, estado, archivo=None, error=None, captura=None):
        with self.base.conexion() as con:
            con.execute('UPDATE descargas_mensuales SET estado=?,archivo=?,error=?,captura=?,fecha_fin=? WHERE id=?',
                        (estado, str(archivo) if archivo else None, error, captura, ahora(), identificador))

    def finalizar(self, ejecucion, error=None):
        with self.base.conexion() as con:
            pendientes = con.execute("SELECT COUNT(*) FROM descargas_mensuales WHERE ejecucion_id=? AND estado='pendiente'", (ejecucion,)).fetchone()[0]
            if pendientes:
                error = error or 'Ejecucion finalizada con tareas sin procesar'
            if error:
                con.execute("UPDATE descargas_mensuales SET estado='error',error=?,fecha_fin=? WHERE ejecucion_id=? AND estado='pendiente'", (error, ahora(), ejecucion))
            errores = con.execute("SELECT COUNT(*) FROM descargas_mensuales WHERE ejecucion_id=? AND estado='error'", (ejecucion,)).fetchone()[0]
            estado = 'error' if error else ('con_errores' if errores else 'completado')
            con.execute('UPDATE ejecuciones_mensuales SET estado=?,fin=?,error=? WHERE id=?', (estado, ahora(), error, ejecucion))
        return self.resumen(ejecucion)

    def resumen(self, ejecucion):
        with self.base.conexion() as con:
            resumen = dict(con.execute('SELECT * FROM ejecuciones_mensuales WHERE id=?', (ejecucion,)).fetchone())
            resumen.pop('reporte_pendiente', None)
            conteos = dict(con.execute('SELECT estado,COUNT(*) FROM descargas_mensuales WHERE ejecucion_id=? GROUP BY estado', (ejecucion,)))
        resumen.update({estado: conteos.get(estado, 0) for estado in ('descargado', 'sin_reporte', 'pendiente')})
        resumen['errores'] = conteos.get('error', 0)
        resumen['seleccionados'] = sum(conteos.values())
        return resumen

    def recuperar_interrumpidas(self, carpeta_reportes):
        from integraciones.seguimiento_mensual import generar_reporte_mensual
        with self.base.conexion() as con:
            filas = con.execute("SELECT id,estado FROM ejecuciones_mensuales WHERE estado='en_proceso' OR reporte_pendiente=1").fetchall()
        for ejecucion, estado in filas:
            if estado == 'en_proceso':
                self.finalizar(ejecucion, 'Ejecucion anterior interrumpida; se conservan las descargas confirmadas')
            generar_reporte_mensual(self, ejecucion, carpeta_reportes)
            self.confirmar_reporte(ejecucion)

    def confirmar_reporte(self, ejecucion):
        with self.base.conexion() as con:
            con.execute('UPDATE ejecuciones_mensuales SET reporte_pendiente=0 WHERE id=?', (ejecucion,))
