"""Entrada Excel y descarga de Reports / Download Monthly Report mediante Chrome."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from openpyxl import load_workbook
from automatizacion.archivos import ContenidoHTML, validar_tabla

from integraciones.filtros import normalizar_encabezado
from integraciones.navegador import SesionCaducada
from integraciones.work_items import script_selectores


class SinReporte(RuntimeError):
    pass


class SolicitudRechazada(RuntimeError):
    pass


@dataclass(frozen=True)
class SolicitudMensual:
    proveedor_taxid: str
    anio: int
    mes: int
    fila: int


def leer_solicitudes(ruta):
    libro = load_workbook(ruta, read_only=True, data_only=True)
    try:
        filas = iter(libro.active.iter_rows(values_only=True))
        cabecera = [normalizar_encabezado(x) for x in next(filas, ())]
        nombres = ('proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta')
        if any(cabecera.count(nombre) != 1 for nombre in nombres):
            raise ValueError('El Excel necesita encabezados unicos: ' + ', '.join(nombres))
        indices = [cabecera.index(nombre) for nombre in nombres]
        solicitudes, vistos = [], set()
        for numero, fila in enumerate(filas, 2):
            if all(x is None or str(x).strip() == '' for x in fila):
                continue
            valores = [fila[i] if i < len(fila) else None for i in indices]
            taxid = str(valores[0] if valores[0] is not None else '').strip()
            if not taxid or isinstance(valores[0], bool):
                raise ValueError(f'Fila {numero}: proveedor_taxid es obligatorio')
            numeros = []
            for nombre, valor in zip(nombres[1:], valores[1:]):
                if isinstance(valor, bool) or not re.fullmatch(r'\d+(?:\.0+)?', str(valor or '').strip()):
                    raise ValueError(f'Fila {numero}: {nombre} debe ser un entero')
                numeros.append(int(float(valor)))
            anio, desde, hasta = numeros
            if not (1900 <= anio <= 9999 and 1 <= desde <= hasta <= 12):
                raise ValueError(f'Fila {numero}: anio invalido o meses fuera de 1 a 12 / en orden inverso')
            for mes in range(desde, hasta + 1):
                clave = (taxid, anio, mes)
                if clave not in vistos:
                    vistos.add(clave)
                    solicitudes.append(SolicitudMensual(taxid, anio, mes, numero))
        if not solicitudes:
            raise ValueError('El Excel no contiene solicitudes de reportes')
        return solicitudes
    finally:
        libro.close()


# Se buscan controles por selector configurable y por sus etiquetas visibles.
# Esto evita depender exclusivamente de IDs de una version del formulario.
FORMULARIO_JS = r"""
    function buscar(selector, patron) {
        var e=document.querySelector(selector);
        if(e)return e;
        var labels=document.querySelectorAll('label');
        for(var i=0;i<labels.length;i++)if(patron.test(labels[i].innerText)) {
            e=document.getElementById(labels[i].htmlFor);
            if(!e)e=labels[i].querySelector('input,select');
            if(!e&&labels[i].parentElement)e=labels[i].parentElement.querySelector('input,select');
            if(e)return e;
        }
        return null;
    }
    var taxid=buscar(selectores.proveedor_taxid,/vendor\s*tax\s*id/i);
    var mes=buscar(selectores.mes,/^\s*month\s*:?\s*$/i);
    var anio=buscar(selectores.anio,/^\s*year\s*:?\s*$/i);
    var botones=document.querySelectorAll('button,input[type=submit],input[type=button],a.btn');
    var enviar=document.querySelector(selectores.enviar);
    if(!enviar)for(var i=0;i<botones.length;i++)if(/download\s+report/i.test(botones[i].innerText||botones[i].value||'')){enviar=botones[i];break;}
    var faltantes=[];
    if(!taxid)faltantes.push('proveedor_taxid');
    if(!mes)faltantes.push('mes');
    if(!anio)faltantes.push('anio');
    if(!enviar)faltantes.push('enviar');
    if(faltantes.length)return JSON.stringify({faltantes:faltantes});
"""

PREPARAR_JS = FORMULARIO_JS + r"""
    function opcion(control, valor, esMes) {
        if(control.tagName!=='SELECT')return String(valor);
        var nombres=['january','february','march','april','may','june','july','august','september','october','november','december'];
        for(var i=0;i<control.options.length;i++) {
            var op=control.options[i], texto=op.text.trim().toLowerCase();
            if(!op.disabled && ((String(op.value).trim()!=='' && Number(op.value)===valor) || texto===String(valor) || (esMes&&texto===nombres[valor-1])))return op.value;
        }
        return null;
    }
    var vm=opcion(mes,solicitud.mes,true), va=opcion(anio,solicitud.anio,false);
    if(vm===null||va===null)return JSON.stringify({rechazo:'El mes o anio solicitado no esta disponible en ACME'});
    function asignar(e,valor){
        e.value=valor;
        e.dispatchEvent(new Event('input',{bubbles:true}));
        e.dispatchEvent(new Event('change',{bubbles:true}));
        if(e.tagName==='SELECT' && window.jQuery && window.jQuery.fn.selectpicker && window.jQuery(e).data('selectpicker'))window.jQuery(e).selectpicker('render');
    }
    asignar(taxid,solicitud.proveedor_taxid);asignar(mes,vm);asignar(anio,va);
    if(!taxid.checkValidity()||!mes.checkValidity()||!anio.checkValidity())return JSON.stringify({rechazo:'ACME no acepta los datos del formulario'});
    window.__acme_report_alert='';
    window.alert=function(mensaje){window.__acme_report_alert=String(mensaje);};
    return JSON.stringify(true);
"""

ENVIAR_JS = FORMULARIO_JS + "enviar.click();return JSON.stringify(true);"
ESTADO_JS = r"""
    var errores=Array.prototype.map.call(document.querySelectorAll(selectores.error),function(e){return e.innerText.trim();}).filter(Boolean);
    return JSON.stringify({alerta:window.__acme_report_alert||'',error:errores.join('; ')});
"""


def validar_archivo(ruta, columnas_requeridas=()):
    try:
        validar_tabla(ruta, columnas_requeridas)
    except ContenidoHTML:
        with ruta.open('rb') as archivo:
            inicio = archivo.read(4096).lower()
        if b'password' in inicio and b'login' in inicio:
            raise SesionCaducada('La descarga devolvio el formulario de acceso') from None
        raise RuntimeError('ACME devolvio HTML en lugar de un reporte') from None
    except Exception as error:
        raise RuntimeError('Reporte invalido: ' + str(error)) from error


class ReportesMensuales:
    def __init__(self, navegador):
        self.navegador = navegador
        self.selectores = navegador.selectores['reportes_mensuales']

    def descargar(self, solicitud, carpeta):
        carpeta = Path(carpeta).resolve()
        carpeta.mkdir(parents=True, exist_ok=False)
        self.navegador.ir(self.navegador.config.url + self.selectores['ruta'])
        self.navegador.carpeta_descargas(carpeta)
        datos = json.dumps({'proveedor_taxid': solicitud.proveedor_taxid, 'anio': solicitud.anio, 'mes': solicitud.mes})

        faltantes = []

        def preparar():
            nonlocal faltantes
            self.navegador.comprobar_sesion()
            respuesta = self.navegador.js('var solicitud=' + datos + ';' + script_selectores(PREPARAR_JS, self.selectores))
            if isinstance(respuesta, dict) and respuesta.get('rechazo'):
                raise SolicitudRechazada(respuesta['rechazo'])
            if isinstance(respuesta, dict) and respuesta.get('faltantes'):
                faltantes = respuesta['faltantes']
                return None
            return respuesta

        try:
            self.navegador.esperar(preparar, 'No aparecio el formulario de reportes mensuales')
        except TimeoutError as error:
            if faltantes:
                raise TimeoutError('No se reconocieron controles del formulario mensual: ' + ', '.join(faltantes)) from error
            raise
        enviado = self.navegador.js(script_selectores(ENVIAR_JS, self.selectores))
        if isinstance(enviado, dict) and enviado.get('faltantes'):
            raise RuntimeError('No se reconocieron controles al enviar el formulario mensual: ' + ', '.join(enviado['faltantes']))
        if not enviado:
            raise RuntimeError('No se pudo enviar el formulario de descarga')

        def completada():
            self.navegador.comprobar_sesion()
            estado = self.navegador.js(script_selectores(ESTADO_JS, self.selectores))
            mensaje = estado.get('alerta') or estado.get('error')
            if mensaje:
                if re.search(r'no\s+(?:report|data|reporte)|(?:report|reporte|data).*(?:not\s+found|does\s+not\s+exist)|no\s+hay\s+(?:datos|reportes?)', mensaje, re.I):
                    raise SinReporte(mensaje)
                # Algunas versiones muestran una alerta de descarga correcta.
                if not re.search(r'download|success', mensaje, re.I):
                    raise SolicitudRechazada(mensaje)
            archivos = [p for p in carpeta.iterdir() if p.is_file()]
            if any(p.suffix.lower() in ('.crdownload', '.tmp', '.part') for p in archivos):
                return None
            if len(archivos) > 1:
                raise RuntimeError('La solicitud genero mas de un archivo')
            if not archivos:
                return None
            validar_archivo(archivos[0], self.selectores.get('columnas_requeridas', ()))
            return archivos[0]

        original = self.navegador.esperar(completada, 'No se completo la descarga del reporte mensual')
        seguro = re.sub(r'[^a-zA-Z0-9_-]', '_', solicitud.proveedor_taxid)[:60]
        huella = hashlib.sha256(solicitud.proveedor_taxid.encode()).hexdigest()[:8]
        fecha_hora = datetime.now().strftime('%d%m%Y-%H%M%S')
        destino = carpeta.parent / f'Monthly-Report-{solicitud.anio}-{solicitud.mes:02d}-{seguro}-{huella}_{fecha_hora}{original.suffix.lower()}'
        if destino.exists():
            raise FileExistsError('Ya existe el destino de esta descarga')
        original.rename(destino)
        carpeta.rmdir()
        return destino
