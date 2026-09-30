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
| Sede vecinal | Vista semanal o mensual, filtro de horas disponibles y formulario que precarga el intervalo elegido. El horario de apertura y cierre se configura en el panel. |
| Transparencia | Montos, movimientos y respaldos se muestran solo a usuarios con una cuenta aprobada. |
| Proyectos | Listado filtrable e historial público de avance físico; montos y fuentes de financiamiento requieren una cuenta aprobada. |
| Plan maestro | Proyectos actuales y futuros, agrupados por eje de trabajo. |
| Documentos | Estatutos, leyes y actas; los archivos de rendición no se publican en el repositorio público. |
| Directorio de servicios | Actividades económicas de vecinas y vecinos, con búsqueda, rubro, llamada directa y WhatsApp. |
| Certificado de residencia | Solicitud individual con documento privado para acreditar domicilio, código de seguimiento y opción de crear una cuenta reutilizando nombre y correo. |
| Quiénes somos | Misión, visión, reseña, socios inscritos y directiva con contacto por cargo. |
| Contacto | Formulario que queda guardado en el panel, más los correos por cargo. |

### Accesibilidad (pensada para adultos mayores)

- Botones **«A+ Texto más grande»** y **«Alto contraste»** en todas las páginas; la preferencia
  queda recordada en el mismo dispositivo.
- Diseño móvil primero, controles táctiles de al menos 48 px, enlace «Ir al contenido» y foco visible.

### Panel de administración (`/panel`)

Tres perfiles:

| Perfil | Puede hacer |
|---|---|
| **Administrador** | Todo, incluida la creación y aprobación de cuentas, contenido institucional, presupuestos, balances, respaldos y permisos delegados al coordinador. |
| **Coordinador** | Reservas, certificados, aprobación del directorio, noticias, documentos no financieros y avances de proyectos. La administración puede delegarle la creación de cuentas; estas quedan pendientes de aprobación. No puede editar usuarios, presupuestos ni finanzas. |
| **Vecino** | Tras la aprobación de la directiva, ingresar a su cuenta y ver reservas enviadas durante la sesión y estados de certificados asociados; el código privado también permite consultar sin iniciar sesión. |

El panel agrupa solicitudes, publicaciones, proyectos, transparencia y configuración. Las
solicitudes nuevas del directorio deben aprobarse antes de publicarse. Desde noticias y documentos
se cargan imágenes y archivos; los respaldos financieros permanecen fuera de la carpeta pública.
Las cuentas creadas desde el registro o desde un certificado quedan pendientes hasta que un
administrador las apruebe en *Panel → Aprobar cuentas vecinales*. La administración también puede
crear cuentas activas desde el panel y decidir si delega la creación al coordinador.

Los documentos para acreditar residencia se guardan en almacenamiento privado del servidor, fuera
de la carpeta pública `static`. Solo las cuentas de administrador y coordinador pueden abrirlos
desde el panel de certificados; los vecinos no tienen acceso al archivo. Por defecto, la base de
datos y estos documentos quedan bajo `instance/`. En un hosting, define `DATA_DIR` con la ruta de
un volumen persistente para conservar la base de datos y los comprobantes tras reinicios o
actualizaciones; también puedes definir `PRIVATE_UPLOAD_FOLDER` para usar otra carpeta privada.
Antes de publicar el sitio, la organización debe completar los textos aprobados de privacidad y
uso desde *Panel → Textos del sitio*.

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

Cubren páginas y formularios, disponibilidad semanal y mensual, documentos privados, aprobación
de cuentas y del directorio, permisos por perfil, cargas de noticias/documentos y visibilidad
financiera para usuarios registrados. Las cargas de prueba se escriben en carpetas temporales.

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
instance/comprobantes_privados/ Documentos de domicilio y respaldos financieros privados
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

4. Configurar `DATA_DIR` en un volumen persistente y respaldar periódicamente la base de datos,
   los comprobantes privados y `static/uploads/`: contienen los datos y archivos del sitio.

---

## Pendiente de definir con la organización

- Logo, placa y paleta oficial de la junta.
- Correos por cargo (`presidencia@`, `secretaria@`, `tesoreria@`…) y enlace al grupo de WhatsApp:
  se cargan desde *Panel → Textos del sitio* y *Panel → Directiva*.
- Horarios exactos de las actividades fijas de la sede (adulto mayor, karate).
- Requisitos definitivos y aviso de privacidad/condiciones de uso aprobados por la contraparte.
  Los campos para integrar esos textos están en *Panel → Textos del sitio*; permanecen sin publicar
  mientras no se entregue una versión aprobada.
- Contenido real: noticias, proyectos, estatutos, actas y rendiciones.
- Al migrar una base existente, los folios numéricos de certificados se reemplazan por códigos
  privados; la directiva deberá facilitar el nuevo código a quienes ya tengan una solicitud pendiente.
- Las cuentas nuevas requieren una clave de al menos 8 caracteres y aprobación manual de la directiva.
