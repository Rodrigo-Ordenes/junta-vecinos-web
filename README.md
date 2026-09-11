# Sitio web · Junta de Vecinos N.° 2 · Cerro Esperanza

Sitio informativo y de trámites para la Junta de Vecinos N.° 2 del Cerro Esperanza.
Construido con **Flask** y **SQLite**, sin dependencias externas de frontend: el CSS y el
JavaScript son propios, así la página carga rápido incluso con señal débil y funciona aunque
no haya acceso a servicios externos.

---

## Qué incluye

### Sitio público

| Sección | Qué resuelve |
|---|---|
| Portada | Accesos grandes a los cuatro trámites más usados, disponibilidad de la sede de la semana, últimas noticias y proyectos. |
| Noticias y actividades | Publicaciones con fecha de actividad y botón para compartir por WhatsApp. |
| Sede vecinal | Calendario semanal con las actividades fijas y las reservas; navegación entre semanas y solicitud de reserva en línea. |
| Transparencia | Balance por período **y balance por actividad realizada** (ingresos, gastos y resultado de cada evento). |
| Proyectos | Listado filtrable por estado (postulado, aprobado, en ejecución, finalizado, rechazado). |
| Plan maestro | Proyectos actuales y futuros, agrupados por eje de trabajo. |
| Documentos | Estatutos, leyes, actas y rendiciones, con archivo descargable o enlace. |
| Directorio de servicios | Actividades económicas de vecinas y vecinos, con búsqueda, rubro, llamada directa y WhatsApp. |
| Certificado de residencia | Requisitos, solicitud en línea y consulta de estado con número de solicitud. |
| Quiénes somos | Misión, visión, reseña, socios inscritos y directiva con contacto por cargo. |
| Contacto | Formulario que queda guardado en el panel, más los correos por cargo. |

### Accesibilidad (pensada para adultos mayores)

- Botones **«A+ Texto más grande»** y **«Alto contraste»** en todas las páginas; la preferencia
  queda recordada en el mismo dispositivo.
- Diseño móvil primero, botones de al menos 44 px, enlace «Ir al contenido» y foco visible.

### Panel de administración (`/panel`)

Tres perfiles:

| Perfil | Puede hacer |
|---|---|
| **Administrador** | Todo, incluidos usuarios del sistema, textos del sitio, proyectos y rendiciones. |
| **Coordinador** (encargado de la sede) | Reservas, certificados, mensajes, noticias, actividades fijas, directorio y documentos. |
| **Vecino** | Ver el estado de sus reservas y certificados en «Mis solicitudes». |

El panel permite aprobar o rechazar reservas, marcar la sede como ocupada, cambiar el estado de
los certificados, subir documentos, cargar ingresos y gastos de cada rendición o actividad, y
editar los textos del sitio sin tocar código.

---

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate        # en Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Luego abrir <http://localhost:5001>. La base de datos se crea sola en `instance/junta.db`
con datos de ejemplo la primera vez.

> El puerto por defecto es el **5001** porque en macOS el 5000 lo ocupa el «Receptor AirPlay».
> Para usar otro: `PORT=8000 python app.py`.

### Usuarios iniciales

| Usuario | Clave | Perfil |
|---|---|---|
| `admin` | `esperanza2026` | Administrador |
| `coordinador` | `esperanza2026` | Coordinador |

> **Importante:** cambiar ambas claves antes de publicar el sitio, desde
> *Panel → Usuarios del sistema*, o definiendo las variables de entorno
> `ADMIN_USER`, `ADMIN_PASSWORD`, `COORD_USER`, `COORD_PASSWORD` y `SECRET_KEY`
> **antes** de la primera ejecución.

---

## Pruebas

```bash
pip install pytest
python -m pytest
```

Cubren las páginas públicas, los formularios (incluido el rechazo de reservas en fechas pasadas
y los choques de horario con las actividades fijas), los permisos de cada perfil y el flujo
completo del panel.

---

## Estructura

```
app.py                 Rutas del sitio público y del panel
db.py                  Esquema SQLite, datos de ejemplo y acceso a datos
templates/             Páginas públicas
templates/panel/       Panel de administración
static/css/style.css   Estilos propios (incluye modo alto contraste)
static/js/sitio.js     Menú, desplegables y accesibilidad
static/uploads/        Archivos subidos desde el panel
instance/junta.db      Base de datos (se crea sola; no se versiona)
test_app.py            Pruebas automatizadas
```

---

## Publicación

1. **Dominio y hosting**: el dominio propuesto es `jv2esperanza.cl`. Sirve cualquier hosting con
   Python (PythonAnywhere, Render, Railway) o un servidor propio con Gunicorn + Nginx.
2. Definir `SECRET_KEY`, `ADMIN_PASSWORD` y `COORD_PASSWORD` como variables de entorno.
3. Servir con Gunicorn en producción:

   ```bash
   pip install gunicorn
   gunicorn -w 2 -b 0.0.0.0:8000 app:app
   ```

4. Respaldar periódicamente `instance/junta.db` y la carpeta `static/uploads/`: ahí está toda la
   información de la junta.

---

## Pendiente de definir con la organización

- Logo, placa y paleta oficial de la junta.
- Correos por cargo (`presidencia@`, `secretaria@`, `tesoreria@`…) y enlace al grupo de WhatsApp:
  se cargan desde *Panel → Textos del sitio* y *Panel → Directiva*.
- Horarios exactos de las actividades fijas de la sede (adulto mayor, karate).
- Requisitos definitivos del certificado de residencia.
- Contenido real: noticias, proyectos, estatutos, actas y rendiciones.
