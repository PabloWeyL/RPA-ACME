"""Gestion manual de la outbox por ejecucion; no ejecuta negocio ni requests web."""
import argparse
from pathlib import Path

from integraciones.cola_correo import ColaCorreo
from integraciones.diagnostico import diagnosticar_correos
from integraciones.weyland.archivos import confinar
from integraciones.weyland.cola_actual import entero_configurado


def _positivo(valor):
    if not valor.isdecimal() or int(valor) < 1:
        raise argparse.ArgumentTypeError('El ID debe ser un entero positivo')
    return int(valor)


def agregar_comandos_correo(comandos):
    for nombre in ('correos-estado', 'correos-reintentar', 'correos-resolver'):
        comando = comandos.add_parser(nombre, help='Gestionar correo sin repetir el proceso ni la entrega web')
        comando.add_argument('--id-ejecucion', required=True, type=_positivo)
        if nombre != 'correos-estado':
            comando.add_argument('--id-correo', required=nombre == 'correos-resolver')
        if nombre == 'correos-resolver':
            comando.add_argument('--destinatario')
            decision = comando.add_mutually_exclusive_group(required=True)
            decision.add_argument('--entregado', action='store_true')
            decision.add_argument('--no-entregado', action='store_true')


def ejecutar_comando_correo(args, valores, raiz, ajustes):
    maquina = entero_configurado(valores, 'BACKEND_MAQUINA_ID')
    ruta = Path(valores.get('BACKEND_TRABAJOS_DIR') or 'temporales/weyland')
    base = (ruta if ruta.is_absolute() else Path(raiz) / ruta).resolve()
    carpeta = confinar(base, f'cola_actual/maquina_{maquina}/ejecuciones/{args.id_ejecucion}/correo')
    if not (carpeta / 'correos.db').is_file():
        if args.comando == 'correos-estado':
            print('No existe una cola de correo para esa ejecucion. No se crearon archivos.')
            return 0
        raise ValueError('No existe una cola de correo para esa ejecucion')
    cola = ColaCorreo(carpeta)
    if args.comando == 'correos-estado':
        print('Estado por destinatario:', cola.resumen())
        for fila in cola.listar():
            print(fila)
        return 0
    if args.comando == 'correos-resolver':
        if not cola.resolver_incierto(args.id_correo, args.entregado, args.destinatario):
            raise ValueError('No hay destinatarios inciertos para esa seleccion')
        print('Resolucion registrada. No se ha enviado correo ni repetido el proceso.')
        return 0
    identificador = args.id_correo
    if identificador and not cola.resumen(solo=identificador):
        raise ValueError('No existe el mensaje seleccionado en esta ejecucion')
    config = ajustes()
    if not config['habilitado']:
        raise ValueError('Correo deshabilitado: no se reenviaran mensajes')
    if identificador:
        cola.reactivar(identificador)
    from integraciones.weyland_notificaciones import enviar
    resumen = cola.procesar(lambda *datos: enviar(config, *datos), solo=identificador)
    print('Estado de correos:', resumen)
    diagnostico = diagnosticar_correos(resumen)
    print(diagnostico.texto())
    return diagnostico.codigo
