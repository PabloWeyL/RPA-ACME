"""Estado durable de la entrega de archivos; separado del historial del RPA."""
import json
from uuid import uuid4

from automatizacion.sqlite import ConexionSQLite, ahora


class HistorialIntercambio:
    def __init__(self, config):
        self.base = ConexionSQLite(config.ruta(config.db))
        self.backend = config.backend_api_url
        self.robot = config.robot_id
        with self.base.conexion() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS intercambios_api (
                id TEXT PRIMARY KEY, backend TEXT NOT NULL, robot_id TEXT NOT NULL,
                remoto_id TEXT NOT NULL, proceso TEXT NOT NULL, nombre_archivo TEXT NOT NULL,
                estado TEXT NOT NULL CHECK(estado IN
                    ('descargando','preparado','ejecutando','pendiente_envio','entregado')),
                entrada TEXT, ejecucion_id TEXT, resumen_json TEXT, reporte TEXT,
                ultimo_error TEXT, actualizado TEXT NOT NULL,
                UNIQUE(backend, robot_id, remoto_id))''')

    def pendiente(self):
        with self.base.conexion() as con:
            fila = con.execute('''SELECT * FROM intercambios_api
                WHERE backend=? AND robot_id=? AND estado!='entregado'
                ORDER BY rowid LIMIT 1''', (self.backend, self.robot)).fetchone()
            return dict(fila) if fila else None

    def obtener(self, identificador):
        with self.base.conexion() as con:
            return dict(con.execute('SELECT * FROM intercambios_api WHERE id=?', (identificador,)).fetchone())

    def registrar(self, solicitud):
        with self.base.conexion() as con:
            fila = con.execute('''SELECT * FROM intercambios_api
                WHERE backend=? AND robot_id=? AND remoto_id=?''',
                (self.backend, self.robot, solicitud['id'])).fetchone()
            if fila:
                if (fila['proceso'], fila['nombre_archivo']) != (solicitud['proceso'], solicitud['nombre_archivo']):
                    raise ValueError('El backend cambio el contenido de una solicitud ya registrada')
                if fila['estado'] == 'entregado':
                    con.execute("UPDATE intercambios_api SET estado='pendiente_envio',actualizado=? WHERE id=?",
                                (ahora(), fila['id']))
                identificador = fila['id']
            else:
                identificador = uuid4().hex
                con.execute('''INSERT INTO intercambios_api
                    (id,backend,robot_id,remoto_id,proceso,nombre_archivo,estado,actualizado)
                    VALUES(?,?,?,?,?,?,'descargando',?)''',
                    (identificador, self.backend, self.robot, solicitud['id'],
                     solicitud['proceso'], solicitud['nombre_archivo'], ahora()))
        return self.obtener(identificador)

    def actualizar(self, identificador, **cambios):
        permitidos = {'estado', 'entrada', 'ejecucion_id', 'resumen_json', 'reporte', 'ultimo_error'}
        if not cambios or not cambios.keys() <= permitidos:
            raise ValueError('Campos de intercambio invalidos')
        cambios['actualizado'] = ahora()
        with self.base.conexion() as con:
            con.execute('UPDATE intercambios_api SET ' + ','.join(c + '=?' for c in cambios) + ' WHERE id=?',
                        (*cambios.values(), identificador))

    def resultado(self, identificador, resumen, reporte=None):
        self.actualizar(identificador, estado='pendiente_envio', resumen_json=json.dumps(resumen, ensure_ascii=False),
                        reporte=str(reporte) if reporte else None, ultimo_error=None)
