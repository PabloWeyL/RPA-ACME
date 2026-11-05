"""Persistencia operacional equivalente en SQLite y Azure SQL."""
from contextlib import contextmanager
from datetime import datetime
import json
import sqlite3
from uuid import uuid4

from integraciones.nube import conectar_sql

TABLA_QUEUE = 'QUEUE_CONTROL_VENCIMIENTOS'
TABLA_DETAILS = 'DETAILS_CONTROL_VENCIMIENTOS'
ESTADOS_QUEUE = ('PENDIENTE', 'EN_EJECUCION', 'FINALIZADO', 'ERROR')
ESTADOS_DETAILS = ('PENDIENTE', 'PROCESADO', 'ERROR', 'OMITIDO')


def ahora():
    return datetime.now().astimezone().isoformat(timespec='seconds')


class BaseDatos:
    def __init__(self, config, secretos=None):
        self.config, self.secretos = config, secretos or {}
        with self.conexion() as conexion:
            texto = 'TEXT' if config.motor_bd == 'sqlite' else 'NVARCHAR(MAX)'
            identificador = 'TEXT' if config.motor_bd == 'sqlite' else 'NVARCHAR(80)'
            estado = 'TEXT' if config.motor_bd == 'sqlite' else 'NVARCHAR(24)'
            columnas_queue = f"""id {identificador} PRIMARY KEY, estado {estado} NOT NULL,
                fecha_creacion NVARCHAR(40) NOT NULL, fecha_inicio NVARCHAR(40), fecha_fin NVARCHAR(40),
                fecha_modificacion NVARCHAR(40) NOT NULL, mensaje_error {texto}, contexto_json {texto} NOT NULL"""
            columnas_details = f"""id {identificador} PRIMARY KEY, id_ejecucion {identificador} NOT NULL
                REFERENCES {TABLA_QUEUE}(id), fila INTEGER NOT NULL, estado {estado} NOT NULL,
                fecha_creacion NVARCHAR(40) NOT NULL, fecha_modificacion NVARCHAR(40) NOT NULL,
                mensaje_error {texto}, datos_json {texto} NOT NULL, UNIQUE(id_ejecucion,fila)"""
            for tabla, columnas in ((TABLA_QUEUE, columnas_queue), (TABLA_DETAILS, columnas_details)):
                sql = f'CREATE TABLE IF NOT EXISTS {tabla} ({columnas})' if config.motor_bd == 'sqlite' else f"IF OBJECT_ID(N'{tabla}', N'U') IS NULL CREATE TABLE {tabla} ({columnas})"
                conexion.cursor().execute(sql)

    @contextmanager
    def conexion(self):
        if self.config.motor_bd == 'azure_sql':
            with conectar_sql(self.config, self.secretos) as conexion:
                yield conexion
            return
        conexion = sqlite3.connect(self.config.raiz / self.config.db_sqlite, timeout=self.config.timeout)
        conexion.execute('PRAGMA foreign_keys=ON')
        try:
            with conexion:
                yield conexion
        finally:
            conexion.close()

    def crear(self, contexto):
        identificador = uuid4().hex
        with self.conexion() as conexion:
            conexion.cursor().execute(f'INSERT INTO {TABLA_QUEUE} (id,estado,fecha_creacion,fecha_modificacion,contexto_json) VALUES(?,?,?,?,?)',
                                     (identificador, 'PENDIENTE', ahora(), ahora(), json.dumps(contexto, ensure_ascii=False)))
        return identificador

    def obtener(self, identificador):
        with self.conexion() as conexion:
            cursor = conexion.cursor()
            cursor.execute(f'SELECT * FROM {TABLA_QUEUE} WHERE id=?', (identificador,))
            fila = cursor.fetchone()
            if fila is None:
                raise ValueError('ID_EJECUCION no existe en este proceso')
            salida = dict(zip((c[0] for c in cursor.description), fila))
        salida['contexto'] = json.loads(salida.pop('contexto_json'))
        return salida

    def actualizar_contexto(self, identificador, contexto):
        with self.conexion() as conexion:
            conexion.cursor().execute(f'UPDATE {TABLA_QUEUE} SET contexto_json=?,fecha_modificacion=? WHERE id=?',
                                     (json.dumps(contexto, ensure_ascii=False), ahora(), identificador))

    def estado(self, identificador, estado, error=None):
        if estado not in ESTADOS_QUEUE:
            raise ValueError('Estado de ejecucion invalido')
        with self.conexion() as conexion:
            conexion.cursor().execute(f"""UPDATE {TABLA_QUEUE} SET estado=?,mensaje_error=?,fecha_modificacion=?,
                fecha_inicio=COALESCE(fecha_inicio,?),fecha_fin=? WHERE id=?""",
                (estado, error, ahora(), ahora() if estado == 'EN_EJECUCION' else None,
                 ahora() if estado in ('FINALIZADO', 'ERROR') else None, identificador))

    def encolar(self, identificador, elementos):
        with self.conexion() as conexion:
            cursor = conexion.cursor()
            for fila, datos in elementos:
                cursor.execute(f'SELECT id FROM {TABLA_DETAILS} WHERE id_ejecucion=? AND fila=?', (identificador, fila))
                if cursor.fetchone() is None:
                    cursor.execute(f'INSERT INTO {TABLA_DETAILS} (id,id_ejecucion,fila,estado,fecha_creacion,fecha_modificacion,datos_json) VALUES(?,?,?,?,?,?,?)',
                                   (identificador + '_' + str(fila), identificador, fila, 'PENDIENTE', ahora(), ahora(), json.dumps(datos, ensure_ascii=False)))

    def detalles(self, identificador):
        with self.conexion() as conexion:
            cursor = conexion.cursor()
            cursor.execute(f'SELECT fila,estado,mensaje_error,datos_json FROM {TABLA_DETAILS} WHERE id_ejecucion=? ORDER BY fila', (identificador,))
            return [{'fila': f[0], 'estado': f[1], 'error': f[2], 'datos': json.loads(f[3])} for f in cursor.fetchall()]

    def resolver(self, identificador, fila, estado, datos, error=None):
        if estado not in ESTADOS_DETAILS:
            raise ValueError('Estado de detalle invalido')
        with self.conexion() as conexion:
            conexion.cursor().execute(f'UPDATE {TABLA_DETAILS} SET estado=?,mensaje_error=?,datos_json=?,fecha_modificacion=? WHERE id_ejecucion=? AND fila=?',
                                     (estado, error, json.dumps(datos, ensure_ascii=False), ahora(), identificador, fila))
