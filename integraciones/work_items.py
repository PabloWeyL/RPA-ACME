import json
from urllib.parse import urlparse


LISTADO_JS = r"""
    var tabla=document.querySelector(selectores.tabla);
    if(!tabla)return JSON.stringify(null);
    var cabecera=Array.prototype.map.call(tabla.querySelectorAll('th'),function(x){return x.innerText.trim().toLowerCase();});
    var campos={wiid:'wiid',descripcion:'description',tipo:'type',estado_web:'status',fecha_web:'date'};
    for(var campo in campos){if(cabecera.indexOf(campos[campo])<0)throw new Error('Cabecera no reconocida: '+campos[campo]);}
    var items=[];
    var filas=tabla.querySelectorAll(selectores.filas);
    for(var i=0;i<filas.length;i++){
        var celdas=filas[i].querySelectorAll('td');
        if(!celdas.length)continue;
        var item={};
        for(var campo in campos){var posicion=cabecera.indexOf(campos[campo]);item[campo]=celdas[posicion]?celdas[posicion].innerText.trim():'';}
        var link=filas[i].querySelector(selectores.enlace_detalle);
        if(!item.wiid||!link)throw new Error('Fila sin WIID o enlace de detalle');
        item.url=link.href;
        items.push(item);
    }
    var next=document.querySelector(selectores.siguiente);
    if(!next){
        var enlaces=document.querySelectorAll(selectores.paginacion);
        for(var i=0;i<enlaces.length;i++){
            if(/^(next|siguiente|[>\u203a\u00bb])\s*[>\u203a\u00bb]*$/i.test(enlaces[i].innerText.trim())){next=enlaces[i];break;}
        }
    }
    return JSON.stringify({items:items,siguiente:next?next.href:null,url:location.href});
"""


DETALLE_JS = r"""
    var contenedor=document.querySelector(selectores.detalle)||document.querySelector(selectores.detalle_respaldo);
    if(!contenedor)return JSON.stringify(null);
    var texto=contenedor.innerText.trim();
    return JSON.stringify(texto||null);
"""


def script_selectores(cuerpo, selectores):
    return "var selectores=" + json.dumps(selectores) + ";" + cuerpo


class WorkItems:
    def __init__(self, navegador):
        self.navegador = navegador
        self.config = navegador.config
        self.selectores = navegador.selectores["work_items"]

    def listar(self):
        url = self.config.url + self.selectores["ruta"]
        visitadas = set()
        unicos = {}
        while url:
            if url in visitadas:
                raise RuntimeError("La paginacion de ACME entro en un ciclo")
            visitadas.add(url)
            self.navegador.ir(url)

            def leer_pagina():
                self.navegador.comprobar_sesion()
                return self.navegador.js(script_selectores(LISTADO_JS, self.selectores))
            pagina = self.navegador.esperar(leer_pagina, "No aparecio la tabla de Work Items")
            for item in pagina["items"]:
                unicos.setdefault(item["wiid"], item)
            url = pagina["siguiente"]
            if len(visitadas) > 10000:
                raise RuntimeError("Se excedio el limite de paginas de proteccion")
        return list(unicos.values())

    def detalle(self, tarea):
        self.navegador.ir(tarea["url"])

        def leer_detalle():
            self.navegador.comprobar_sesion()
            texto = self.navegador.js(script_selectores(DETALLE_JS, self.selectores))
            # Verificar que se abrio el identificador solicitado y no otra pagina.
            url = self.navegador.estado()["url"]
            if urlparse(url).path.rstrip("/").split("/")[-1] != str(tarea["wiid"]):
                return None
            return texto if texto and str(tarea["wiid"]) in texto else None
        return self.navegador.esperar(leer_detalle, "El detalle no cargo o esta vacio")
