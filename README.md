# Sitio Web · Junta de Vecinos N.° 2 Cerro Esperanza

Sitio web hecho en Python (Flask) para centralizar comunicación, noticias, transparencia y proyectos de la junta de vecinos.

## Qué incluye

- **Página de inicio** con resumen de noticias y proyectos recientes.
- **Noticias y actividades**: listado y detalle de cada publicación.
- **Proyectos**: postulados, aprobados, rechazados, en ejecución y finalizados, con filtro por estado.
- **Transparencia**: tabla de rendiciones de cuentas por período, con enlace a documentos.
- **Directiva**: listado de la directiva vigente.
- **Contacto**: formulario que guarda los mensajes en la base de datos.
- **Panel de administración** (`/admin`) protegido con usuario y clave, donde una persona sin conocimientos técnicos puede agregar o editar noticias, proyectos, rendiciones, directiva y revisar los mensajes recibidos — sin tocar código.

## Requisitos

- Python 3.10 o superior.

## Instalación

```bash
cd junta-vecinos-web
python3 -m venv venv
source venv/bin/activate        # En Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Ejecutar el sitio

```bash
python app.py
```

Luego abre en el navegador: http://localhost:5000

## Acceder al panel de administración

Ve a http://localhost:5000/admin

- **Usuario:** `admin`
- **Clave:** `esperanza2026`

**Importante:** antes de publicar el sitio en internet, cambia estas credenciales. Puedes hacerlo definiendo variables de entorno al ejecutar la aplicación:

```bash
ADMIN_USER=tu_usuario ADMIN_PASSWORD=tu_clave_segura SECRET_KEY=una_clave_larga_y_aleatoria python app.py
```

## Estructura del proyecto

```
junta-vecinos-web/
├── app.py                 # Rutas y configuración principal
├── models.py               # Modelos de la base de datos
├── requirements.txt
├── instance/
│   └── junta.db             # Base de datos SQLite (se crea automáticamente)
├── static/
│   └── css/style.css
└── templates/
    ├── base.html
    ├── index.html
    ├── noticias.html
    ├── noticia_detalle.html
    ├── proyectos.html
    ├── transparencia.html
    ├── directiva.html
    ├── contacto.html
    └── login.html
```

## Cómo publicar el sitio en internet

Este proyecto está listo para desplegarse en cualquier servicio compatible con Flask (por ejemplo, Render, Railway, PythonAnywhere o un servidor propio con Gunicorn + Nginx). Los pasos generales son:

1. Subir el código a un repositorio (por ejemplo GitHub).
2. Configurar las variables de entorno `ADMIN_USER`, `ADMIN_PASSWORD` y `SECRET_KEY` en el servicio de hosting.
3. Usar un servidor de producción como Gunicorn en lugar de `python app.py`:
   ```bash
   pip install gunicorn
   gunicorn app:app
   ```

## Próximos pasos sugeridos

- Reemplazar los datos de ejemplo (noticias, proyectos, directiva) por la información real desde el panel `/admin`.
- Subir el bosquejo/logo oficial de la junta y ajustar colores en `static/css/style.css`.
- Agregar fotos reales a las noticias (campo "imagen_url" en el panel de administración).
- Subir los documentos PDF de rendición de cuentas a un servicio de almacenamiento y pegar el enlace en el campo "archivo_url".
