from automatizacion.sqlite import ConexionSQLite, ahora
from datetime import datetime
from uuid import uuid4


class BaseDatos(ConexionSQLite):
    def __init__(self, ruta):
        super().__init__(ruta)
        with self.conexion() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS ejecuciones (
                    id TEXT PRIMARY KEY, inicio TEXT NOT NULL, fin TEXT,
                    estado TEXT NOT NULL, limite INTEGER, coincidencias INTEGER NOT NULL DEFAULT 0,
                    seleccionados INTEGER NOT NULL DEFAULT 0, leidos INTEGER NOT NULL DEFAULT 0,
                    errores INTEGER NOT NULL DEFAULT 0, error TEXT
                );
                CREATE TABLE IF NOT EXISTS details (
                    id INTEGER PRIMARY KEY, ejecucion_id TEXT NOT NULL REFERENCES ejecuciones(id),
                    archivo TEXT NOT NULL, ruta TEXT NOT NULL, filtros_json TEXT,
                    estado TEXT NOT NULL CHECK(estado IN ('pendiente','leido','error')),
                    fecha_creacion TEXT NOT NULL, fecha_fin TEXT, error TEXT
                );
                CREATE TABLE IF NOT EXISTS cola (
                    id INTEGER PRIMARY KEY, ejecucion_id TEXT NOT NULL REFERENCES ejecuciones(id),
                    detail_id INTEGER NOT NULL REFERENCES details(id), wiid TEXT NOT NULL,
                    descripcion TEXT NOT NULL, tipo TEXT NOT NULL, estado_web TEXT NOT NULL,
                    fecha_web TEXT NOT NULL, url TEXT NOT NULL,
                    estado TEXT NOT NULL DEFAULT 'pendiente' CHECK(estado IN ('pendiente','leido','error')),
                    intentos INTEGER NOT NULL DEFAULT 0, error TEXT, captura TEXT,
                    fecha_fin TEXT, UNIQUE(ejecucion_id, wiid)
                );
                CREATE TABLE IF NOT EXISTS resultados (
                    id INTEGER PRIMARY KEY, cola_id INTEGER NOT NULL UNIQUE REFERENCES cola(id),
                    wiid TEXT NOT NULL, descripcion TEXT NOT NULL, tipo TEXT NOT NULL,
                    estado_web TEXT NOT NULL, fecha_web TEXT NOT NULL, url TEXT NOT NULL,
                    detalle TEXT NOT NULL, fecha_consulta TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cola_ejecucion_estado ON cola(ejecucion_id,estado);
                CREATE INDEX IF NOT EXISTS idx_details_ejecucion ON details(ejecucion_id);
            """)
            columnas = {fila['name'] for fila in con.execute('PRAGMA table_info(ejecuciones)')}
            if 'reporte_pendiente' not in columnas:
                con.execute('ALTER TABLE ejecuciones ADD COLUMN reporte_pendiente INTEGER NOT NULL DEFAULT 0')
                con.execute("UPDATE ejecuciones SET reporte_pendiente=1 WHERE estado='en_proceso'")

    def iniciar(self, ruta, limite):
        identificador = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8]
        with self.conexion() as con:
            con.execute("INSERT INTO ejecuciones(id,inicio,estado,limite,reporte_pendiente) VALUES(?,?,'en_proceso',?,1)", (identificador, ahora(), limite))
            archivo_id = con.execute(
                "INSERT INTO details(ejecucion_id,archivo,ruta,estado,fecha_creacion) VALUES(?,?,?,'pendiente',?)",
                (identificador, ruta.name, str(ruta.resolve()), ahora()),
            ).lastrowid
        return identificador, archivo_id

    def recuperar_interrumpidas(self):
        """Invocar solo tras adquirir el bloqueo exclusivo del proyecto."""
        with self.conexion() as con:
            filas = con.execute("SELECT id,estado FROM ejecuciones WHERE estado='en_proceso' OR reporte_pendiente=1").fetchall()
        for identificador, estado in filas:
            if estado == 'en_proceso':
                self.finalizar(identificador, "Ejecucion anterior interrumpida antes de completar el cierre")
        return [fila[0] for fila in filas]

    def confirmar_reporte(self, ejecucion):
        with self.conexion() as con:
            con.execute('UPDATE ejecuciones SET reporte_pendiente=0 WHERE id=?', (ejecucion,))

    def archivo_estado(self, archivo_id, estado, filtros_json=None, error=None):
        with self.conexion() as con:
            con.execute("UPDATE details SET estado=?,filtros_json=?,error=?,fecha_fin=? WHERE id=?", (estado, filtros_json, error, ahora(), archivo_id))

    def encolar(self, ejecucion, archivo_id, items, coincidencias):
        with self.conexion() as con:
            con.executemany(
                "INSERT INTO cola(ejecucion_id,detail_id,wiid,descripcion,tipo,estado_web,fecha_web,url) VALUES(?,?,?,?,?,?,?,?)",
                [(ejecucion, archivo_id, item["wiid"], item["descripcion"], item["tipo"], item["estado_web"], item["fecha_web"], item["url"]) for item in items],
            )
            con.execute("UPDATE ejecuciones SET coincidencias=?,seleccionados=? WHERE id=?", (coincidencias, len(items), ejecucion))

    def tareas(self, ejecucion):
        with self.conexion() as con:
            return [dict(row) for row in con.execute("SELECT * FROM cola WHERE ejecucion_id=? AND estado='pendiente' ORDER BY id", (ejecucion,))]

    def intento(self, tarea_id, numero):
        with self.conexion() as con:
            con.execute("UPDATE cola SET intentos=? WHERE id=?", (numero, tarea_id))

    def guardar(self, tarea, detalle):
        if not detalle.strip():
            raise ValueError("El detalle esta vacio")
        fecha = ahora()
        with self.conexion() as con:
            con.execute(
                "INSERT INTO resultados(cola_id,wiid,descripcion,tipo,estado_web,fecha_web,url,detalle,fecha_consulta) VALUES(?,?,?,?,?,?,?,?,?)",
                (tarea["id"], tarea["wiid"], tarea["descripcion"], tarea["tipo"], tarea["estado_web"], tarea["fecha_web"], tarea["url"], detalle, fecha),
            )
            con.execute("UPDATE cola SET estado='leido',error=NULL,fecha_fin=? WHERE id=?", (fecha, tarea["id"]))

    def fallar_tarea(self, tarea_id, error, captura):
        with self.conexion() as con:
            con.execute("UPDATE cola SET estado='error',error=?,captura=?,fecha_fin=? WHERE id=?", (error, captura, ahora(), tarea_id))

    def finalizar(self, ejecucion, error=None):
        with self.conexion() as con:
            pendientes = con.execute("SELECT COUNT(*) FROM cola WHERE ejecucion_id=? AND estado='pendiente'", (ejecucion,)).fetchone()[0]
            if pendientes:
                error = error or 'Ejecucion finalizada con tareas sin procesar'
                con.execute("UPDATE cola SET estado='error',error=?,fecha_fin=? WHERE ejecucion_id=? AND estado='pendiente'", (error, ahora(), ejecucion))
            fila = con.execute("SELECT COUNT(CASE WHEN estado='leido' THEN 1 END) leidos, COUNT(CASE WHEN estado='error' THEN 1 END) errores FROM cola WHERE ejecucion_id=?", (ejecucion,)).fetchone()
            estado = "error" if error else ("con_errores" if fila["errores"] else "completado")
            con.execute("UPDATE ejecuciones SET fin=?,estado=?,leidos=?,errores=?,error=? WHERE id=?", (ahora(), estado, fila["leidos"], fila["errores"], error, ejecucion))
            resumen = dict(con.execute("SELECT * FROM ejecuciones WHERE id=?", (ejecucion,)).fetchone())
            resumen.pop('reporte_pendiente', None)
            return resumen

    def datos_reporte(self, ejecucion):
        with self.conexion() as con:
            resultados = con.execute("SELECT r.wiid,r.descripcion,r.tipo,r.estado_web,r.fecha_web,r.url,r.detalle,r.fecha_consulta FROM resultados r JOIN cola c ON c.id=r.cola_id WHERE c.ejecucion_id=? ORDER BY c.id", (ejecucion,)).fetchall()
            errores = con.execute("SELECT wiid,estado,intentos,error,captura FROM cola WHERE ejecucion_id=? AND estado!='leido' ORDER BY id", (ejecucion,)).fetchall()
            resumen = dict(con.execute("SELECT * FROM ejecuciones WHERE id=?", (ejecucion,)).fetchone())
            resumen.pop('reporte_pendiente', None)
            archivo = dict(con.execute("SELECT archivo,ruta,estado,filtros_json,error FROM details WHERE ejecucion_id=?", (ejecucion,)).fetchone())
            return resultados, errores, resumen, archivo
