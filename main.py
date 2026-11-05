import argparse
import sys

from config.configuracion import cargar_configuracion
from config.base_datos import BaseDatos
from integraciones.bloqueo import bloqueo_proyecto
from integraciones.proceso import consultar
from integraciones.reportes import generar_reporte
from integraciones.proceso_mensual import descargar_reportes
from integraciones.historial_mensual import HistorialMensual
from integraciones.entradas import descubrir_entradas


def entero_positivo(valor):
    numero = int(valor)
    if numero <= 0:
        raise argparse.ArgumentTypeError("El limite debe ser mayor que cero")
    return numero


def main(argv=None):
    parser = argparse.ArgumentParser(description="RPA ACME: consulta de Work Items y descarga de reportes mensuales")
    comandos = parser.add_subparsers(dest="comando")
    todos = comandos.add_parser("procesar", help="Procesar todos los Excel compatibles de CARPETA_INPUTS (predeterminado)")
    todos.add_argument("--limite", type=entero_positivo, help="Maximo de tareas por archivo y proceso")
    consulta = comandos.add_parser("consultar", help="Consultar Work Items con todos los Excel compatibles de CARPETA_INPUTS")
    consulta.add_argument("--limite", type=entero_positivo, help="Maximo de detalles a consultar; sin limite por defecto")
    mensuales = comandos.add_parser("descargar-reportes", help="Descargar reportes usando todos los Excel compatibles de CARPETA_INPUTS")
    mensuales.add_argument("--limite", type=entero_positivo, help="Maximo de reportes mensuales a solicitar")
    args = parser.parse_args(argv)
    config = None
    try:
        config = cargar_configuracion()
        if not config.modulo_scraping:
            print("MODULO_SCRAPING=false: automatizacion desactivada; no se iniciaron tareas ni correos")
            return 0
        with bloqueo_proyecto(config.root / ".runtime/consulta.lock"):
            # Recuperar historial existente incluso si ya no quedan entradas.
            # Sin historial ni entradas no se crea una base nueva.
            if config.ruta(config.db).is_file():
                base = BaseDatos(config.ruta(config.db))
                for identificador in base.recuperar_interrumpidas():
                    generar_reporte(base, identificador, config.carpeta_reportes)
                    base.confirmar_reporte(identificador)
                HistorialMensual(config.ruta(config.db)).recuperar_interrumpidas(config.carpeta_reportes)
            entradas, omitidos = descubrir_entradas(config.ruta(config.inputs), args.comando or "procesar")
            for ruta, motivo in omitidos:
                print("Omitido:", ruta.name, "-", motivo)
            if not entradas:
                raise ValueError("No hay Excel compatibles en la carpeta de entrada para el comando solicitado")
            fallos = 0
            for entrada in entradas:
                print("Procesando:", entrada.ruta.name, "-", entrada.comando)
                try:
                    limite = getattr(args, "limite", None)
                    if entrada.comando == "descargar-reportes":
                        resumen, reporte = descargar_reportes(config, entrada.ruta, limite)
                    else:
                        resumen, reporte = consultar(config, limite, entrada=entrada.ruta)
                    print("Reporte:", reporte)
                    if resumen["estado"] != "completado":
                        fallos += 1
                except Exception as error:
                    fallos += 1
                    print("Error en", entrada.ruta.name + ":", config.ocultar(error), file=sys.stderr)
        print("Procesos ejecutados:", len(entradas), "- Con errores:", fallos)
        return 1 if fallos else 0
    except Exception as error:
        print("Error:", config.ocultar(error) if config else "No se pudo cargar la configuracion", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
