"""Respaldo completo del sitio: base de datos, documentos privados e imágenes subidas.

Uso (dentro del entorno virtual del proyecto):

    python respaldo.py                 # crea respaldos/respaldo-AAAA-MM-DD_HHMM.zip
    python respaldo.py --conservar 30  # además borra los respaldos más antiguos

En el hosting se programa como tarea diaria. Copia la base con la función de
respaldo de SQLite (segura aunque el sitio esté funcionando) y no con un copiado
simple del archivo, que puede quedar corrupto si alguien está guardando algo.

Para restaurar: detener el sitio, descomprimir el .zip y dejar ``junta.db`` en la
carpeta de datos (``instance/`` o ``DATA_DIR``), ``comprobantes_privados/`` en la
misma carpeta y ``uploads/`` en ``static/uploads/``. Luego volver a iniciar el sitio.
"""

import os
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime

import entorno

entorno.cargar()  # DATA_DIR viene de .env cuando se ejecuta como tarea programada

import db as base  # noqa: E402

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CARPETA_RESPALDOS = os.environ.get("RESPALDOS_DIR", os.path.join(base.INSTANCE_DIR, "respaldos"))


def _agregar_carpeta(archivo_zip, carpeta, nombre_en_zip):
    if not os.path.isdir(carpeta):
        return
    for raiz, _, archivos in os.walk(carpeta):
        for nombre in archivos:
            ruta = os.path.join(raiz, nombre)
            if os.path.islink(ruta):
                continue
            relativa = os.path.relpath(ruta, carpeta)
            archivo_zip.write(ruta, os.path.join(nombre_en_zip, relativa))


def crear_respaldo(destino, carpeta_privada=None, carpeta_subidas=None):
    """Escribe en ``destino`` un .zip con la base y los archivos del sitio."""
    carpeta_privada = carpeta_privada or os.environ.get(
        "PRIVATE_UPLOAD_FOLDER", os.path.join(base.INSTANCE_DIR, "comprobantes_privados")
    )
    carpeta_subidas = carpeta_subidas or os.path.join(BASE_DIR, "static", "uploads")
    with tempfile.TemporaryDirectory() as temporal:
        copia_db = os.path.join(temporal, "junta.db")
        origen = sqlite3.connect(base.DB_PATH)
        copia = sqlite3.connect(copia_db)
        try:
            origen.backup(copia)
        finally:
            copia.close()
            origen.close()
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as archivo_zip:
            archivo_zip.write(copia_db, "junta.db")
            _agregar_carpeta(archivo_zip, carpeta_privada, "comprobantes_privados")
            _agregar_carpeta(archivo_zip, carpeta_subidas, "uploads")
    return destino


def limpiar_antiguos(carpeta, conservar):
    respaldos = sorted(
        n for n in os.listdir(carpeta) if n.startswith("respaldo-") and n.endswith(".zip")
    )
    for nombre in respaldos[:-conservar] if conservar > 0 else []:
        os.remove(os.path.join(carpeta, nombre))


def main(argumentos):
    conservar = 30
    if "--conservar" in argumentos:
        conservar = int(argumentos[argumentos.index("--conservar") + 1])
    os.makedirs(CARPETA_RESPALDOS, exist_ok=True)
    nombre = f"respaldo-{datetime.now().strftime('%Y-%m-%d_%H%M')}.zip"
    destino = crear_respaldo(os.path.join(CARPETA_RESPALDOS, nombre))
    limpiar_antiguos(CARPETA_RESPALDOS, conservar)
    print(f"Respaldo creado: {destino} ({os.path.getsize(destino) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
