# Protección de datos del bot

El repositorio contiene código, documentación técnica y plantillas con credenciales
vacías. Entradas, exportaciones, imágenes, bases de datos, correos, logs, sesiones
del navegador y entornos privados permanecen fuera de Git.

Después de clonar, activar los controles locales:

```bash
git config core.hooksPath .githooks
```

Antes de confirmar, revisar el índice preparado:

```bash
python tools/verificar_publicacion.py
```

El hook pre-commit revisa el índice. El hook pre-push revisa los commits que se
envían. La comprobación bloquea rutas operativas, binarios, tokens reconocibles,
claves privadas y credenciales locales que encuentre en el contenido. Solo
informa rutas y motivos; nunca imprime los valores encontrados.

Las plantillas `.env*.example` mantienen vacíos usuarios, contraseñas y tokens.
Las referencias `SECRET_*` identifican secretos de Key Vault y no contienen sus
valores. Los datos sintéticos de pruebas deben ser claramente ficticios.

El workflow de GitHub repite la comprobación con permisos de lectura y sin
conservar credenciales de checkout. Esa comprobación ocurre después de subir;
los hooks locales son la barrera previa. Activar su check como requisito de rama
en la configuración de GitHub si se desea impedir merges que fallen.

Estos controles complementan la revisión humana; no detectan todos los formatos
de secretos. `.gitignore` no elimina archivos ya versionados ni limpia commits
anteriores. Si se expone una credencial, revocarla y evaluar la limpieza del
historial; no basta con borrar su archivo en el siguiente commit.
