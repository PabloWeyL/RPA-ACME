from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from config.configuracion import Configuracion
from integraciones.entradas import descubrir_entradas
import main as aplicacion


class EntradasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='acme-entradas-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.carpeta = self.root / 'input'
        self.carpeta.mkdir()

    def excel(self, nombre, encabezados, filas=(), otra_hoja=False):
        libro = Workbook()
        libro.active.append(encabezados)
        for fila in filas:
            libro.active.append(fila)
        if otra_hoja:
            libro.create_sheet('instrucciones').append(['No es una entrada'])
            libro.active = 1
        ruta = self.carpeta / nombre
        libro.save(ruta)
        libro.close()
        return ruta

    def test_clasifica_por_encabezados_no_por_nombre(self):
        self.excel('a_cualquier_nombre.XLSX', [' ESTADO ', 'Descripcion extra', ' TIPO '])
        self.excel('b_otro.xlsm', ['ANIO', 'MES_HASTA', 'PROVEEDOR_TAXID', 'MES_DESDE'])
        entradas, omitidos = descubrir_entradas(self.carpeta)
        self.assertEqual([(e.ruta.name, e.comando) for e in entradas], [
            ('a_cualquier_nombre.XLSX', 'consultar'), ('b_otro.xlsm', 'descargar-reportes')])
        self.assertEqual(omitidos, [])

    def test_omite_duplicados_incompletos_corruptos_y_hoja_inactiva(self):
        self.excel('duplicado.xlsx', ['tipo', 'estado', 'ESTADO'])
        self.excel('incompleto.xlsx', ['tipo'])
        self.excel('inactiva.xlsx', ['tipo', 'estado'], otra_hoja=True)
        (self.carpeta / 'corrupto.xlsx').write_bytes(b'No es un Excel')
        (self.carpeta / 'antiguo.xls').write_bytes(b'xls')
        (self.carpeta / '~$bloqueado.xlsx').write_bytes(b'archivo temporal')
        (self.carpeta / 'notas.txt').write_text('sin datos')
        sub = self.carpeta / 'subcarpeta'
        sub.mkdir()
        (sub / 'anidado.xlsx').write_bytes(b'no recorrer')
        entradas, omitidos = descubrir_entradas(self.carpeta)
        self.assertEqual(entradas, [])
        self.assertEqual({r.name for r, _ in omitidos}, {'duplicado.xlsx', 'incompleto.xlsx', 'inactiva.xlsx', 'corrupto.xlsx', 'antiguo.xls'})

    def test_filtro_por_comando_y_archivo_con_ambos_esquemas(self):
        self.excel('ambos.xlsx', ['tipo', 'estado', 'proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta'])
        entradas, _ = descubrir_entradas(self.carpeta)
        self.assertEqual([e.comando for e in entradas], ['consultar', 'descargar-reportes'])
        entradas, _ = descubrir_entradas(self.carpeta, 'descargar-reportes')
        self.assertEqual([e.comando for e in entradas], ['descargar-reportes'])

    def test_carpeta_vacia_o_inexistente(self):
        self.assertEqual(descubrir_entradas(self.carpeta), ([], []))
        with self.assertRaisesRegex(ValueError, 'No existe'):
            descubrir_entradas(self.root / 'inexistente')

    def test_aliases_mensuales_se_descubren_y_se_leen_con_la_misma_regla(self):
        from integraciones.reportes_mensuales import leer_solicitudes
        archivo = self.excel('periodos.xlsx', ['proveedor_taxid', 'a\u00f1o', 'mes_inicio', 'mes_fin'], [('A', 2025, 1, 2)])
        entradas, omitidos = descubrir_entradas(self.carpeta)
        self.assertEqual([e.comando for e in entradas], ['descargar-reportes'])
        self.assertEqual(omitidos, [])
        self.assertEqual([s.mes for s in leer_solicitudes(archivo)], [1, 2])
        self.excel('duplicado.xlsx', ['proveedor_taxid', 'anio', 'a\u00f1o', 'mes_inicio', 'mes_fin'])
        entradas, omitidos = descubrir_entradas(self.carpeta)
        self.assertEqual(len(entradas), 1)
        self.assertEqual(omitidos[0][0].name, 'duplicado.xlsx')

    def ejecutar(self, argumentos=(), fallo=None):
        self.config = Configuracion(root=self.root)
        with ExitStack() as stack:
            stack.enter_context(patch('main.cargar_configuracion', return_value=self.config))
            stack.enter_context(patch('builtins.print'))
            base = stack.enter_context(patch('main.BaseDatos'))
            base.return_value.recuperar_interrumpidas.return_value = []
            stack.enter_context(patch('main.HistorialMensual'))
            consulta = stack.enter_context(patch('main.consultar', return_value=({'estado': 'completado'}, self.root / 'consulta.xlsx')))
            mensual = stack.enter_context(patch('main.descargar_reportes', return_value=({'estado': 'completado'}, self.root / 'mensual.xlsx')))
            if fallo is not None:
                consulta.side_effect = fallo
            codigo = aplicacion.main(list(argumentos) or ['procesar'])
        return codigo, consulta, mensual, base

    def test_procesar_todos_pasa_la_ruta_correcta(self):
        a = self.excel('a.xlsx', ['tipo', 'estado'])
        b = self.excel('b.xlsx', ['tipo', 'estado'])
        c = self.excel('c.xlsx', ['proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta'])
        self.excel('ignorar.xlsx', ['otras'])
        codigo, consulta, mensual, _ = self.ejecutar()
        self.assertEqual(codigo, 0)
        self.assertEqual([c.kwargs['entrada'] for c in consulta.call_args_list], [a, b])
        mensual.assert_called_once_with(self.config, c, None)

    def test_limite_es_por_archivo_y_el_fallo_no_detiene_los_demas(self):
        self.excel('a.xlsx', ['tipo', 'estado'])
        self.excel('b.xlsx', ['tipo', 'estado'])
        self.excel('c.xlsx', ['proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta'])
        codigo, consulta, mensual, _ = self.ejecutar(['procesar', '--limite', '2'], fallo=[RuntimeError('Fallo de reporte'), ({'estado': 'completado'}, 'reporte.xlsx')])
        self.assertEqual(codigo, 1)
        self.assertEqual(consulta.call_count, 2)
        self.assertTrue(all(c.args[1] == 2 for c in consulta.call_args_list))
        self.assertEqual(mensual.call_count, 1)
        self.assertEqual(mensual.call_args.args[2], 2)

    def test_comando_mensual_elige_solo_los_compatibles(self):
        self.excel('filtros.xlsx', ['tipo', 'estado'])
        self.excel('mensuales.xlsx', ['proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta'])
        codigo, consulta, mensual, _ = self.ejecutar(['descargar-reportes'])
        self.assertEqual(codigo, 0)
        consulta.assert_not_called()
        self.assertEqual(mensual.call_count, 1)

    def test_sin_compatibles_no_crea_ejecuciones(self):
        self.excel('invalido.xlsx', ['otra'])
        codigo, consulta, mensual, base = self.ejecutar()
        self.assertEqual(codigo, 1)
        consulta.assert_not_called()
        mensual.assert_not_called()
        base.assert_not_called()

    def test_recupera_excel_pendiente_aunque_no_queden_entradas(self):
        from config.base_datos import BaseDatos
        config = Configuracion(root=self.root, inputs=Path('input'))
        base = BaseDatos(config.ruta(config.db))
        ejecucion, _ = base.iniciar(self.root / 'entrada.xlsx', None)
        base.finalizar(ejecucion)
        with patch.object(aplicacion, 'cargar_configuracion', return_value=config), patch.object(aplicacion, 'consultar') as consultar:
            self.assertEqual(aplicacion.main(['procesar']), 1)  # Sin nuevas entradas.
        consultar.assert_not_called()
        self.assertEqual(len(list(config.carpeta_reportes.glob('work_items_*.xlsx'))), 1)
        self.assertEqual(base.recuperar_interrumpidas(), [])

    def test_consulta_real_registra_el_archivo_descubierto(self):
        from config.base_datos import BaseDatos
        from integraciones.proceso import consultar
        from test_acme import NavegadorFalso, item
        archivo = self.excel('nombre_libre.xlsx', ['tipo', 'estado'], [('WI5', 'Open')])
        config = Configuracion(root=self.root)
        resumen, reporte = consultar(config, fabrica_navegador=lambda c: NavegadorFalso(c, items=[item(1)]), entrada=archivo)
        self.assertEqual(resumen['leidos'], 1)
        self.assertTrue(reporte.is_file())
        with BaseDatos(config.ruta(config.db)).conexion() as con:
            fila = con.execute('SELECT archivo,ruta FROM details').fetchone()
            self.assertEqual(tuple(fila), (archivo.name, str(archivo.resolve())))

    def test_cli_real_procesa_todos_los_nombres_y_genera_reportes_con_fecha(self):
        from config.base_datos import BaseDatos
        from config.configuracion import ROOT
        from integraciones.proceso import consultar
        from integraciones.proceso_mensual import descargar_reportes
        from test_acme import NavegadorFalso, item
        from test_reportes_mensuales import NavegadorMensualFalso
        archivos = [self.excel('Pedidos de hoy.xlsx', ['tipo', 'estado']),
                    self.excel('Cualquier otro nombre.xlsm', ['estado', 'tipo'])]
        mensual = self.excel('Proveedor de prueba.xlsx', ['proveedor_taxid', 'anio', 'mes_desde', 'mes_hasta'], [('A', 2025, 1, 1)])
        config = Configuracion(root=self.root)
        self.assertEqual(config.inputs, Path('input'))
        (self.root / 'config').mkdir()
        (self.root / 'config/selectores.json').write_text((ROOT / 'config/selectores.json').read_text(encoding='utf-8'))
        def consultar_falso(config, limite, entrada):
            return consultar(config, limite, entrada=entrada, fabrica_navegador=lambda c: NavegadorFalso(c, items=[item(1)]))
        def mensual_falso(config, entrada, limite):
            return descargar_reportes(config, entrada, limite, fabrica_navegador=NavegadorMensualFalso)
        with patch.object(aplicacion, 'cargar_configuracion', return_value=config), patch.object(aplicacion, 'consultar', side_effect=consultar_falso), patch.object(aplicacion, 'descargar_reportes', side_effect=mensual_falso):
            self.assertEqual(aplicacion.main(['procesar']), 0)
        reportes = list(config.carpeta_reportes.glob('*.xlsx'))
        self.assertEqual(len(reportes), 3)
        for reporte in reportes:
            self.assertRegex(reporte.name, r'^(work_items|reportes_mensuales)_\d{8}-\d{6}_[a-f0-9]{12}\.xlsx$')
        with BaseDatos(config.ruta(config.db)).conexion() as conexion:
            rutas = {fila[0] for fila in conexion.execute('SELECT ruta FROM details')}
            solicitud = conexion.execute('SELECT archivo FROM ejecuciones_mensuales').fetchone()[0]
        self.assertEqual(rutas, {str(archivo.resolve()) for archivo in archivos})
        self.assertEqual(solicitud, str(mensual.resolve()))


if __name__ == '__main__':
    unittest.main()
