"""Carga las variables de configuración desde el archivo ``.env`` del proyecto.

En el hosting las claves (SECRET_KEY, ADMIN_PASSWORD, SMTP_…) se guardan en ``.env``,
que nunca se sube a GitHub. Este módulo lo lee sin librerías externas. Las variables
que ya existan en el entorno no se sobrescriben.
"""

import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def cargar(ruta=None):
    ruta = ruta or os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(ruta):
        return
    with open(ruta, encoding="utf-8") as archivo:
        for linea in archivo:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            valor = valor.strip()
            if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
                valor = valor[1:-1]
            os.environ.setdefault(clave.strip(), valor)
