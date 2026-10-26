"""Punto de entrada para el hosting (PythonAnywhere, Gunicorn u otro servidor WSGI).

Lee la configuración de ``.env`` antes de iniciar la aplicación.
"""

import entorno

entorno.cargar()

from app import app as application  # noqa: E402

app = application
