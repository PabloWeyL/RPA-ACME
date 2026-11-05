from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

import pytest
from openpyxl import Workbook, load_workbook

import main as aplicacion
from configuracion import cargar_configuracion
from automatizacion.operacion import bloqueo, rutas_ejecucion
from automatizacion.trazabilidad import crear_logger, cerrar_logger, configurar_archivo
from config.base_datos import BaseDatos, TABLA_QUEUE, TABLA_DETAILS
from integraciones.ejecucion import ejecutar_pipeline
from integraciones.entradas import leer_inventario, leer_confirmaciones
from integraciones.inventario import clasificar, leer_fecha, leer_cantidad
from integraciones.monitor import Parametros, obtener_parametros, validar_parametros
from integraciones.nube import _odbc, conectar_sql, obtener_secretos
from integraciones.reportes import crear_plantilla


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    # No heredar variables de una cuenta de desarrollo ni servicios reales.
    plantilla = Path(__file__).resolve().parents[1] / '.env.example'
    archivo = tmp_path / '.env'
    archivo.write_text(plantilla.read_text(encoding='utf-8'), encoding='utf-8')
    with patch.dict('os.environ', {}, clear=True):
        config = cargar_configuracion(True, archivo)
    entrada = tmp_path / 'input/inventario_ejemplo.csv'
    entrada.parent.mkdir()
    entrada.write_text('codigo,producto,lote,cantidad,fecha_vencimiento\nA,Leche,L1,2,2026-11-01\nB,Arroz,L2,3,2027-01-01\n', encoding='utf-8')
    parametros = tmp_path / 'parametros.json'
    parametros.write_text('{"FECHA_PROCESO":"2026-11-04","DIAS_ALERTA":30}')
    monkeypatch.setattr(aplicacion, 'cargar_configuracion', lambda **kwargs: config)
    return config, parametros


@pytest.fixture
def logger():
    salida = crear_logger()
    yield salida
    cerrar_logger(salida)


def filas_queue(base):
    with base.conexion() as conexion:
        return conexion.cursor().execute(f'SELECT id,estado FROM {TABLA_QUEUE} ORDER BY fecha_creacion').fetchall()


@pytest.mark.parametrize('fecha,estado,dias', [(date(2026,11,3),'vencido',-1), (date(2026,11,4),'vence_hoy',0),
                                              (date(2026,12,4),'por_vencer',30), (date(2026,12,5),'vigente',31), (None,'pendiente_revision',None)])
def test_limites(fecha, estado, dias):
    assert clasificar(fecha,date(2026,11,4),30) == (estado,dias)


@pytest.mark.parametrize('valor', ['2024-02-29','29/02/2024','29-02-2024',datetime(2024,2,29)])
def test_fechas_validas(valor):
    assert leer_fecha(valor) == date(2024,2,29)


@pytest.mark.parametrize('valor', ['29/02/2025','11/26','2026-11','10/11/26'])
def test_fechas_invalidas(valor):
    with pytest.raises(ValueError): leer_fecha(valor)


@pytest.mark.parametrize('valor', ['NaN','Infinity',-1,None,'1,500',True])
def test_cantidades_invalidas(valor):
    with pytest.raises(ValueError): leer_cantidad(valor)


def test_cantidad_decimal_y_cero():
    assert leer_cantidad('1.5') == Decimal('1.5')
    assert leer_cantidad(0) == 0


def test_excel_fecha_nativa_y_codigos_texto(tmp_path):
    ruta=tmp_path/'entrada.xlsx'
    libro=Workbook()
    libro.active.append(['codigo','producto','lote','cantidad','fecha_vencimiento'])
    libro.active.append(['001','Leche','A',12,datetime(2026,11,5)])
    libro.save(ruta)
    libro.close()
    registros,errores=leer_inventario(ruta)
    assert registros[0].codigo=='001' and registros[0].fecha==date(2026,11,5) and not errores


def test_csv_semicolon_y_distintos_lotes(tmp_path):
    ruta=tmp_path/'entrada.csv'
    ruta.write_text('codigo;producto;lote;cantidad;fecha_vencimiento\n001;Leche;A;12;2026-11-05\n001;Leche;B;2;2026-12-10\n',encoding='utf-8-sig')
    registros,errores=leer_inventario(ruta)
    assert len(registros)==2 and not errores


def test_confirmaciones_desconocidas_y_repetidas(tmp_path):
    ruta=tmp_path/'revision.csv'
    for filas in ('A,OTRO,2026-11-05\n','A,L1,2026-11-05\nA,L1,2026-11-06\n'):
        ruta.write_text('codigo,lote,fecha_confirmada\n'+filas)
        with pytest.raises(ValueError): leer_confirmaciones(ruta,{('A','L1')})


def test_plantillas_no_sobrescriben(tmp_path):
    for extension in ('.csv','.xlsx'):
        ruta=tmp_path/('plantilla'+extension)
        crear_plantilla(ruta)
        with pytest.raises(FileExistsError): crear_plantilla(ruta)


def test_config_sin_fallbacks_y_sin_secretos(entorno):
    config,_=entorno
    archivo=config.raiz/'.env'
    original=archivo.read_text()
    for cambio in (original.replace('MAX_REINTENTOS=3','MAX_REINTENTOS='),original+'\nEMAIL_PASSWORD=NO_ES_UN_SECRETO_REAL\n'):
        archivo.write_text(cambio)
        with patch.dict('os.environ',{},clear=True),pytest.raises(ValueError):
            cargar_configuracion(True,archivo)


def test_sqlite_nombre_sin_ruta_y_produccion_no_simulada(entorno):
    config,_=entorno
    archivo=config.raiz/'.env'
    texto=archivo.read_text()
    for cambio in (texto.replace('DB_SQLITE=control_vencimientos.db','DB_SQLITE=otro/base.db'),texto.replace('ENTORNO=DESARROLLO','ENTORNO=PRODUCCION')):
        archivo.write_text(cambio)
        with patch.dict('os.environ',{},clear=True),pytest.raises(ValueError): cargar_configuracion(True,archivo)


def test_monitor_obligatorio_antes_de_bd(entorno):
    config,_=entorno
    with patch.dict('os.environ',{},clear=True),pytest.raises(ValueError,match='KEYVAULT_NAME'):
        cargar_configuracion(False,config.raiz/'.env')
    with patch.object(aplicacion,'obtener_parametros',side_effect=ValueError('Falta DIAS_ALERTA')),patch.object(aplicacion,'BaseDatos') as base:
        assert aplicacion.main(['procesar','--prueba-local'])==1
    base.assert_not_called()


def test_parametros_negocio_sin_fallback_y_cero_valido():
    assert validar_parametros({'FECHA_PROCESO':'2026-11-04','DIAS_ALERTA':0}).dias_alerta==0
    for datos in ({},{'FECHA_PROCESO':'2026-11-04'},{'FECHA_PROCESO':'','DIAS_ALERTA':30},{'FECHA_PROCESO':'2026-11-04','DIAS_ALERTA':-1}):
        with pytest.raises(ValueError):validar_parametros(datos)


def test_flujo_local_estados_backup_y_exit_tecnico(entorno):
    config,parametros=entorno
    original=config.entrada.read_bytes()
    assert aplicacion.main(['procesar','--prueba-local','--parametros-prueba',str(parametros)])==0
    base=BaseDatos(config)
    identificador,estado=filas_queue(base)[0]
    assert estado=='FINALIZADO'
    assert [d['estado'] for d in base.detalles(identificador)]==['PROCESADO','PROCESADO']
    assert config.entrada.read_bytes()==original
    contexto=base.obtener(identificador)['contexto']
    rutas=rutas_ejecucion(config,identificador,contexto['fecha_ejecucion'])
    reportes=list(rutas['reportes'].glob('*.xlsx'))
    assert len(reportes)==1
    libro=load_workbook(reportes[0]);assert libro['alertas'].max_row==2;libro.close()
    zip_path=next(config.ruta_historico.rglob('*.zip'))
    with ZipFile(zip_path) as archivo:
        assert all(n.split('/')[0] in ('input','procesados','reportes') for n in archivo.namelist())
        assert archivo.testzip() is None
    contenido=next(config.ruta_logs.glob('*.log')).read_text(encoding='utf-8')
    assert '[ID:SIN_ID]' in contenido and f'[ID:{identificador}]' in contenido


def test_error_item_y_omitido_no_son_fallo_tecnico(entorno):
    config,parametros=entorno
    config.entrada.write_text('codigo,producto,lote,cantidad,fecha_vencimiento\nA,Leche,L1,-1,2026-11-04\nB,Arroz,L2,3,\n')
    assert aplicacion.main(['procesar','--prueba-local','--parametros-prueba',str(parametros)])==0
    base=BaseDatos(config);identificador,estado=filas_queue(base)[0]
    assert estado=='FINALIZADO'
    assert [d['estado'] for d in base.detalles(identificador)]==['ERROR','OMITIDO']


def test_respaldo_fallido_error_y_reanudacion_no_repite_items(entorno,monkeypatch):
    config,parametros=entorno
    args=['procesar','--prueba-local','--parametros-prueba',str(parametros)]
    with patch('integraciones.ejecucion.respaldar',side_effect=OSError('Disco lleno')):
        assert aplicacion.main(args)==1
    base=BaseDatos(config);identificador,estado=filas_queue(base)[0]
    assert estado=='ERROR'
    config.entrada.unlink()  # La copia asociada al ID es la fuente de reanudacion.
    monkeypatch.setattr(aplicacion,'cargar_configuracion',lambda **kwargs:replace(config,id_ejecucion=identificador))
    with patch('integraciones.ejecucion.evaluar') as evaluar:
        assert aplicacion.main(args)==0
    evaluar.assert_not_called()
    assert base.obtener(identificador)['estado']=='FINALIZADO'
    assert len(base.detalles(identificador))==2
    with patch('integraciones.ejecucion.preparar_entrada') as preparar:
        assert aplicacion.main(args)==0
    preparar.assert_not_called()


def test_interrupcion_externa_deja_pendientes_y_reanuda(entorno,logger):
    import integraciones.ejecucion as ejecucion
    config,_=entorno
    base=BaseDatos(config)
    parametros=Parametros(date(2026,11,4),30)
    original=ejecucion.evaluar
    with patch.object(ejecucion,'evaluar',side_effect=[original(*_args_para_evaluar()),KeyboardInterrupt()]):
        with pytest.raises(KeyboardInterrupt): ejecutar_pipeline(config,parametros,base,logger,config.entrada)
    identificador,estado=filas_queue(base)[0]
    assert estado=='EN_EJECUCION'
    assert [d['estado'] for d in base.detalles(identificador)]==['PROCESADO','PENDIENTE']
    with patch.object(ejecucion,'evaluar',wraps=original) as evaluar:
        ejecutar_pipeline(replace(config,id_ejecucion=identificador),parametros,base,logger,config.entrada)
    assert evaluar.call_count==1


def _args_para_evaluar():
    from integraciones.inventario import Registro
    return Registro('A','Leche','L1',Decimal(2),date(2026,11,1),2),{},date(2026,11,4),30


def test_reanudacion_rechaza_parametros_distintos(entorno,logger):
    config,_=entorno;base=BaseDatos(config);parametros=Parametros(date(2026,11,4),30)
    identificador=ejecutar_pipeline(config,parametros,base,logger,config.entrada)
    with pytest.raises(ValueError,match='parametros'):
        ejecutar_pipeline(replace(config,id_ejecucion=identificador),Parametros(date(2026,11,4),15),base,logger,config.entrada)


def test_bloqueo_evitar_dos_instancias(tmp_path):
    with bloqueo(tmp_path/'lock'):
        with pytest.raises(RuntimeError):
            with bloqueo(tmp_path/'lock'):pass


def test_redaccion_incluye_traceback(entorno,logger):
    config,_=entorno;configurar_archivo(logger,config)
    logger.secretos.append('secreto_ficticio')
    try:raise RuntimeError('Fallo secreto_ficticio')
    except RuntimeError:logger.exception('Error secreto_ficticio')
    texto=next(config.ruta_logs.glob('*.log')).read_text(encoding='utf-8')
    assert 'Traceback' in texto and 'secreto_ficticio' not in texto and '[OCULTO]' in texto


def test_odbc_escapa_separadores_y_valida_tls(entorno):
    config,_=entorno
    config=replace(config,valores={**config.valores,'DB_SERVER':'servidor','DB_NAME':'base'})
    conexion=Mock();conexion.cursor.return_value=Mock()
    with patch('pyodbc.connect',return_value=conexion) as conectar:
        with conectar_sql(config,{'SECRET_DB_USER':'usuario','SECRET_DB_PASSWORD':'abc};def'}):pass
    cadena=conectar.call_args.args[0]
    assert 'Encrypt=yes' in cadena and 'TrustServerCertificate=no' in cadena and 'abc}};def' in cadena
    conexion.close.assert_called_once()
    assert _odbc('a}b')=='{a}}b}'


def test_monitor_contrato_sql_y_parametro_enlazado(entorno):
    config,_=entorno
    config=replace(config,prueba_local=False,valores={**config.valores,'MONITOR_SP_PARAMETROS':'dbo.SP_PARAMETROS',
                    'MONITOR_DB_SERVER':'servidor','MONITOR_DB_NAME':'monitor'})
    conexion=Mock();cursor=conexion.cursor.return_value
    cursor.description=[('nombre',),('valor',)];cursor.fetchall.return_value=[('FECHA_PROCESO','2026-11-04'),('DIAS_ALERTA','15')]
    from contextlib import contextmanager
    @contextmanager
    def conectar(*args,**kwargs):yield conexion
    with patch('integraciones.monitor.conectar_sql',conectar):
        parametros=obtener_parametros(config,{})
    assert parametros.dias_alerta==15
    assert cursor.execute.call_args.args==('EXEC dbo.SP_PARAMETROS @nombre_proceso=?','CONTROL_VENCIMIENTOS')


def test_keyvault_ref_configurable_no_valores_en_archivos(entorno):
    config,_=entorno
    config=replace(config,prueba_local=False,valores={**config.valores,'KEYVAULT_NAME':'vault-pruebas',
                   'SECRET_MONITOR_DB_USER':'monitor-user','SECRET_MONITOR_DB_PASSWORD':'monitor-pass'})
    cliente=Mock();cliente.__enter__=Mock(return_value=cliente);cliente.__exit__=Mock(return_value=False)
    cliente.get_secret.side_effect=[Mock(value='usuario_ficticio'),Mock(value='password_ficticio')]
    with patch('azure.identity.DefaultAzureCredential') as credencial,patch('azure.keyvault.secrets.SecretClient',return_value=cliente):
        secretos=obtener_secretos(config)
    assert secretos['SECRET_MONITOR_DB_PASSWORD']=='password_ficticio'
    assert [c.args[0] for c in cliente.get_secret.call_args_list]==['monitor-user','monitor-pass']
    credencial.return_value.close.assert_called_once()
    assert 'password_ficticio' not in (config.raiz/'.env').read_text()


def test_reintentos_no_reintentan_datos_invalidos_y_registran_intentos(entorno,logger):
    from automatizacion.reintentos import reintentar
    config,_=entorno
    accion=Mock(side_effect=[TimeoutError('Temporal'),42])
    with patch('automatizacion.reintentos.time.sleep') as pausa:
        assert reintentar(accion,config,logger,lambda e:isinstance(e,TimeoutError))==42
    pausa.assert_called_once_with(config.espera_reintento)
    error=ValueError('Dato incorrecto')
    accion=Mock(side_effect=error)
    with pytest.raises(ValueError):reintentar(accion,config,logger,lambda e:isinstance(e,TimeoutError))
    assert accion.call_count==1 and error.intentos_realizados==1


def test_correo_fallido_no_oculta_error_original(entorno,logger):
    from integraciones.correo import notificar_error
    config,_=entorno
    config=replace(config,modulo_correo=True,valores={**config.valores,'SMTP_SERVER':'smtp.example.test',
                   'SMTP_PORT':'587','LISTA_DISTRIBUCION_ERROR':'a@example.test;b@example.test'})
    servidor=Mock();servidor.__enter__=Mock(return_value=servidor);servidor.__exit__=Mock(return_value=False)
    servidor.send_message.side_effect=OSError('Fallo de red')
    error=RuntimeError('Error original')
    configurar_archivo(logger,config)
    with patch('integraciones.correo.smtplib.SMTP',return_value=servidor):
        notificar_error(config,{'SECRET_SMTP_USER':'robot@example.test','SECRET_SMTP_PASSWORD':'ficticio'},logger,'123','RESPALDO',error)
    contenido=next(config.ruta_logs.glob('*.log')).read_text(encoding='utf-8')
    assert 'Fallo el aviso' in contenido and str(error)=='Error original'
    mensaje=servidor.send_message.call_args.args[0]
    assert 'Intentos realizados: 1' in mensaje.get_content() and 'Error original' in mensaje.get_content()


def test_reanudacion_rechaza_copia_de_entrada_modificada(entorno,logger):
    config,_=entorno;base=BaseDatos(config);parametros=Parametros(date(2026,11,4),30)
    with patch('integraciones.ejecucion.respaldar',side_effect=OSError('Disco lleno')):
        with pytest.raises(OSError):ejecutar_pipeline(config,parametros,base,logger,config.entrada)
    identificador,_=filas_queue(base)[0]
    contexto=base.obtener(identificador)['contexto']
    rutas=rutas_ejecucion(config,identificador,contexto['fecha_ejecucion'])
    next(rutas['input'].glob('inventario.*')).write_text('Contenido modificado')
    with pytest.raises(ValueError,match='copia de entrada'):
        ejecutar_pipeline(replace(config,id_ejecucion=identificador),parametros,base,logger,config.entrada)


def test_log_retiene_seis_meses_y_no_toca_archivos_ajenos(entorno,logger):
    config,_=entorno
    config.ruta_logs.mkdir()
    antiguo=config.ruta_logs/'control_vencimientos_2020_01.log'
    ajeno=config.ruta_logs/'otro_proceso_2020_01.log'
    antiguo.write_text('Viejo');ajeno.write_text('Conservar')
    configurar_archivo(logger,config)
    assert not antiguo.exists() and ajeno.exists()


def test_azure_sql_utiliza_tablas_y_operaciones_equivalentes(entorno):
    config,_=entorno
    config=replace(config,motor_bd='azure_sql')
    conexion=Mock();cursor=conexion.cursor.return_value
    from contextlib import contextmanager
    @contextmanager
    def conectar(*args,**kwargs):yield conexion
    with patch('config.base_datos.conectar_sql',conectar):
        base=BaseDatos(config,{})
        identificador=base.crear({'fecha_proceso':'2026-11-04'})
        base.estado(identificador,'EN_EJECUCION')
    llamadas=[c.args[0] for c in cursor.execute.call_args_list]
    assert any('IF OBJECT_ID' in c for c in llamadas)
    assert any(TABLA_QUEUE in c and 'INSERT INTO' in c for c in llamadas)
    assert any(TABLA_DETAILS in c for c in llamadas)


def test_modulo_desactivado_se_registra_sin_consultar_azure(entorno,monkeypatch):
    config,_=entorno
    monkeypatch.setattr(aplicacion,'cargar_configuracion',lambda **kwargs:replace(config,modulo_procesamiento=False))
    with patch.object(aplicacion,'obtener_secretos') as secretos,patch.object(aplicacion,'BaseDatos') as base:
        assert aplicacion.main([])==0
    secretos.assert_not_called();base.assert_not_called()
    assert 'Modulo 1 desactivado' in next(config.ruta_logs.glob('*.log')).read_text(encoding='utf-8')
