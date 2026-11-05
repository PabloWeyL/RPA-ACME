from dataclasses import dataclass
import unicodedata

from openpyxl import load_workbook


def normalizar(valor):
    texto = unicodedata.normalize("NFKD", str(valor or "").strip().casefold())
    return "".join(letra for letra in texto if not unicodedata.combining(letra))


def normalizar_encabezado(valor):
    nombre = normalizar(valor)
    return {'ano': 'anio', 'mes_inicio': 'mes_desde', 'mes_fin': 'mes_hasta'}.get(nombre, nombre)


@dataclass(frozen=True)
class Filtro:
    tipo: str = ""
    estado: str = ""

    def coincide(self, item):
        return (not self.tipo or normalizar(item["tipo"]) == normalizar(self.tipo)) and (
            not self.estado or normalizar(item["estado_web"]) == normalizar(self.estado)
        )


def leer_filtros(ruta):
    libro = load_workbook(ruta, read_only=True, data_only=True)
    try:
        filas = list(libro.active.iter_rows(values_only=True))
    finally:
        libro.close()
    if not filas:
        raise ValueError("El Excel de filtros esta vacio")
    cabecera = [normalizar_encabezado(celda) for celda in filas[0]]
    if cabecera.count("tipo") != 1 or cabecera.count("estado") != 1:
        raise ValueError("El Excel necesita encabezados unicos 'tipo' y 'estado'")
    indices = [cabecera.index(nombre) for nombre in ("tipo", "estado")]
    filtros = []
    for fila in filas[1:]:
        valores = [str(fila[i]).strip() if i < len(fila) and fila[i] is not None else "" for i in indices]
        filtro = Filtro(*valores)
        if filtro not in filtros:
            filtros.append(filtro)
    return filtros or [Filtro()]


def seleccionar_items(items, filtros, limite=None):
    unicos = {}
    for item in items:
        if any(filtro.coincide(item) for filtro in filtros):
            unicos.setdefault(str(item["wiid"]), item)
    def orden(item):
        wiid = str(item["wiid"])
        return (0, int(wiid)) if wiid.isdigit() else (1, wiid)
    seleccionados = sorted(unicos.values(), key=orden)
    return seleccionados[:limite] if limite is not None else seleccionados
