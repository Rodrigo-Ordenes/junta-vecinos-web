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

### Usuarios iniciales (solo en desarrollo)

| Usuario | Clave | Perfil |
|---|---|---|
| `admin` | `esperanza2026` | Administrador |
| `coordinador` | `esperanza2026` | Coordinador |

> En producción estas cuentas de prueba no existen: el administrador se crea con las claves de
> `.env` y el sitio no arranca si faltan (ver «Publicación»).

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

## Publicación (PythonAnywhere)

Se recomienda **PythonAnywhere, plan Developer (US$10 al mes)**: guarda los archivos de forma
permanente (la base SQLite no se pierde al actualizar), tiene panel web en vez de consola y permite
programar el respaldo diario. El plan gratuito sirve para una demo, pero no permite enviar correos
ni usar un dominio propio. Un dominio `.cl` (opcional) se compra aparte en nic.cl.

### Primera publicación

1. Crear la cuenta en pythonanywhere.com con el correo de la junta y contratar el plan.
2. Abrir una consola **Bash** (pestaña *Consoles*) y ejecutar:

   ```bash
   git clone https://github.com/Rodrigo-Ordenes/junta-vecinos-web.git
   cd junta-vecinos-web
   python3.12 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   cp .env.ejemplo .env && nano .env      # completar claves, correo y DATA_DIR
   mkdir -p ~/datos-junta
   ```

   En `.env`: `DATA_DIR=/home/TU_USUARIO/datos-junta`, una `SECRET_KEY` generada con
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`, la clave y el correo del
   administrador y, para los avisos por correo, los datos SMTP (por ejemplo una cuenta Gmail de la
   junta con *contraseña de aplicación*).
3. Pestaña **Web → Add a new web app → Manual configuration → Python 3.12**. Luego:
   - *Virtualenv*: `/home/TU_USUARIO/junta-vecinos-web/venv`
   - *Static files*: URL `/static/` → carpeta `/home/TU_USUARIO/junta-vecinos-web/static`
   - *Force HTTPS*: activado.
   - Editar el *WSGI configuration file* y dejar solo:

     ```python
     import sys
     sys.path.insert(0, "/home/TU_USUARIO/junta-vecinos-web")
     from wsgi import application  # noqa
     ```
4. Botón **Reload**. Si algo falla, el motivo aparece en el *error log* de la pestaña Web (por
   ejemplo, si falta una clave en `.env` el sitio no arranca y lo dice).
5. Entrar a `/login` con la cuenta de administración, revisar **Textos del sitio** (correo de
   contacto, aporte de la sede, calles) y usar **«Enviarme un correo de prueba»**.
6. Cargar el padrón en **Panel → Carga masiva del padrón**: primero con «solo simular», revisar los
   errores y luego cargarlo de verdad. Cada socio entra con su RUT sin puntos ni guion como usuario
   y los dígitos del RUT sin dígito verificador como clave; en el primer ingreso debe cambiarla y
   registrar su correo.
7. Pestaña **Tasks**: crear una tarea diaria (por ejemplo 04:00) con
   `cd ~/junta-vecinos-web && venv/bin/python respaldo.py --conservar 30`.

### Actualizar el sitio con una nueva versión

```bash
cd ~/junta-vecinos-web && venv/bin/python respaldo.py   # respaldo antes de cambiar nada
git pull && venv/bin/pip install -r requirements.txt
```

Luego **Reload** en la pestaña Web. Las columnas y tablas nuevas se agregan solas a la base al
arrancar; no se borra información.

---

## Base de datos: cómo está y cómo cuidarla

- Es **SQLite**: un solo archivo, `junta.db`, dentro de `DATA_DIR` (o `instance/` en desarrollo).
  Para el tamaño de la junta (cientos de socios, pocas visitas simultáneas) no hace falta un
  servidor de base de datos aparte.
- El esquema está en `db.py`. Al iniciar, el sitio crea lo que falte y agrega columnas nuevas
  automáticamente. Nunca borra ni renombra columnas: un cambio así requiere un script manual y un
  respaldo previo.
- En modo producción (`JUNTA_ENTORNO=produccion`) **no se cargan datos de ejemplo** y el sitio se
  niega a arrancar con las claves de prueba. Sí se cargan una vez los datos base reales (puntos del
  mapa y el enlace a la Ley 19.418), que luego se administran desde el panel.
- Los archivos viven en tres lugares, y los tres entran en el respaldo: `junta.db`,
  `comprobantes_privados/` (documentos de domicilio y respaldos financieros, nunca públicos) y
  `static/uploads/` (imágenes).
- **Respaldos**: `respaldo.py` copia la base con la función de respaldo de SQLite (segura con el
  sitio funcionando) y comprime todo en `DATA_DIR/respaldos/`. Además, el administrador puede
  descargar un respaldo desde *Textos del sitio*. Conviene guardar una copia fuera del hosting al
  menos una vez al mes y **probar al menos una vez que se puede restaurar** (instrucciones dentro de
  `respaldo.py`).
- **Datos personales**: el padrón, las planillas y `.env` nunca se suben a GitHub (`.gitignore`
  los excluye). El cargador solo guarda los datos que el sitio usa (no estado civil, nacionalidad
  ni profesión). La Ley 21.719 de protección de datos rige plenamente desde diciembre de 2026:
  publicar el aviso de privacidad en *Textos del sitio* y limitar quién tiene cuentas del panel.
- **Registro de socios (Ley 19.418, art. 15)**: en *Padrón y cumpleaños* se dan de baja socios
  con su motivo y se descargan el registro vigente (copia de marzo para el secretario municipal) y
  las altas y bajas del semestre.

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
