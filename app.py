"""Sitio web de la Junta de Vecinos N.° 2 · Cerro Esperanza.

Aplicación Flask con SQLite. Incluye el sitio público (noticias, proyectos,
transparencia, documentos, sede vecinal con reservas, directorio de servicios y
certificado de residencia) y un panel de administración en español con tres
perfiles: administrador, coordinador y vecino.
"""

import os
import unicodedata
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import db as base
from db import (
    CATEGORIAS_DOCUMENTO,
    DIAS_SEMANA,
    ESTADOS_CERTIFICADO,
    ESTADOS_PROYECTO,
    ESTADOS_RESERVA,
    ROLES,
    RUBROS_SERVICIO,
    consultar,
    ejecutar,
    obtener_config,
)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CARPETA_SUBIDAS = os.path.join(BASE_DIR, "static", "uploads")
EXTENSIONES_PERMITIDAS = {
    "pdf", "doc", "docx", "xls", "xlsx", "odt", "ods",
    "jpg", "jpeg", "png", "webp", "gif",
}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")
app.config["ADMIN_USER"] = os.environ.get("ADMIN_USER", "admin")
app.config["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD", "esperanza2026")
app.config["COORD_USER"] = os.environ.get("COORD_USER", "coordinador")
app.config["COORD_PASSWORD"] = os.environ.get("COORD_PASSWORD", "esperanza2026")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB por archivo
app.teardown_appcontext(base.close_db)

os.makedirs(CARPETA_SUBIDAS, exist_ok=True)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def a_fecha(valor):
    if not valor:
        return None
    if isinstance(valor, date):
        return valor
    try:
        return datetime.fromisoformat(str(valor)).date()
    except ValueError:
        return None


def fecha_larga(valor):
    f = a_fecha(valor)
    if not f:
        return ""
    return f"{f.day} de {MESES[f.month - 1]} de {f.year}"


def fecha_corta(valor):
    f = a_fecha(valor)
    return f.strftime("%d-%m-%Y") if f else ""


def moneda(valor):
    try:
        numero = int(valor or 0)
    except (TypeError, ValueError):
        return "$0"
    return "$" + f"{numero:,}".replace(",", ".")


def solo_digitos(texto):
    return "".join(c for c in str(texto or "") if c.isdigit())


def sin_acentos(texto):
    return "".join(
        c for c in unicodedata.normalize("NFD", str(texto or ""))
        if unicodedata.category(c) != "Mn"
    ).lower()


def clase_estado(estado):
    return "badge-" + sin_acentos(estado).replace(" ", "-")


app.jinja_env.filters["clase_estado"] = clase_estado
app.jinja_env.filters["fecha_larga"] = fecha_larga
app.jinja_env.filters["fecha_corta"] = fecha_corta
app.jinja_env.filters["moneda"] = moneda
app.jinja_env.filters["solo_digitos"] = solo_digitos


def usuario_actual():
    uid = session.get("usuario_id")
    if not uid:
        return None
    return consultar("SELECT * FROM usuarios WHERE id = ? AND activo = 1", (uid,), uno=True)


def puede(recurso, usuario=None):
    usuario = usuario or usuario_actual()
    if not usuario:
        return False
    if usuario["rol"] == "administrador":
        return True
    if usuario["rol"] == "coordinador":
        return recurso in PERMISOS_COORDINADOR
    return False


def login_requerido(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not usuario_actual():
            flash("Primero debes iniciar sesión.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def permiso_requerido(recurso):
    def decorador(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            usuario = usuario_actual()
            if not usuario:
                flash("Primero debes iniciar sesión.", "error")
                return redirect(url_for("login", next=request.path))
            if not puede(recurso, usuario):
                flash("Tu perfil no tiene permiso para esta sección.", "error")
                return redirect(url_for("panel"))
            return view(*args, **kwargs)
        return wrapper
    return decorador


def guardar_archivo(campo):
    archivo = request.files.get(campo)
    if not archivo or not archivo.filename:
        return None
    nombre = secure_filename(archivo.filename)
    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    if extension not in EXTENSIONES_PERMITIDAS:
        flash(f"El archivo «{archivo.filename}» no es de un tipo permitido.", "error")
        return None
    marca = datetime.now().strftime("%Y%m%d%H%M%S")
    nombre_final = f"{marca}_{nombre}"
    archivo.save(os.path.join(CARPETA_SUBIDAS, nombre_final))
    return nombre_final


# ---------------------------------------------------------------------------
# Definición de los recursos administrables (CRUD genérico del panel)
# ---------------------------------------------------------------------------
def campo(nombre, etiqueta, tipo="texto", **extra):
    d = {"nombre": nombre, "etiqueta": etiqueta, "tipo": tipo, "requerido": False, "ayuda": ""}
    d.update(extra)
    return d


RECURSOS = {
    "noticias": {
        "titulo": "Noticias y actividades",
        "singular": "noticia",
        "tabla": "noticias",
        "orden": "fecha_publicacion DESC",
        "columnas": [("titulo", "Título"), ("fecha_evento", "Fecha de la actividad"),
                     ("publicado", "Publicada")],
        "campos": [
            campo("titulo", "Título", requerido=True),
            campo("resumen", "Resumen corto", "textarea", requerido=True,
                  ayuda="Una o dos líneas; es lo que se ve en la portada."),
            campo("contenido", "Texto completo", "textarea", requerido=True, filas=8),
            campo("fecha_evento", "Fecha de la actividad (opcional)", "fecha"),
            campo("imagen_url", "Enlace a una imagen (opcional)"),
            campo("publicado", "Publicada en el sitio", "casilla", defecto=1),
        ],
        "extra_nuevo": {"fecha_publicacion": lambda: datetime.now().isoformat(timespec="seconds")},
    },
    "proyectos": {
        "titulo": "Proyectos",
        "singular": "proyecto",
        "tabla": "proyectos",
        "orden": "fecha_actualizacion DESC",
        "columnas": [("nombre", "Nombre"), ("estado", "Estado"), ("horizonte", "Horizonte"),
                     ("monto", "Monto")],
        "campos": [
            campo("nombre", "Nombre del proyecto", requerido=True),
            campo("descripcion", "Descripción", "textarea", requerido=True),
            campo("estado", "Estado", "opciones", opciones=ESTADOS_PROYECTO, requerido=True),
            campo("horizonte", "Horizonte", "opciones", opciones=["Actual", "Futuro"],
                  requerido=True, ayuda="«Futuro» aparece en el plan maestro como proyección."),
            campo("eje", "Eje del plan maestro",
                  ayuda="Por ejemplo: Seguridad, Espacios públicos, Sede vecinal."),
            campo("fuente_financiamiento", "Fuente de financiamiento"),
            campo("monto", "Monto en pesos", "numero"),
        ],
        "extra_siempre": {"fecha_actualizacion": lambda: datetime.now().isoformat(timespec="seconds")},
    },
    "documentos": {
        "titulo": "Documentos",
        "singular": "documento",
        "tabla": "documentos",
        "orden": "fecha_publicacion DESC",
        "columnas": [("titulo", "Título"), ("categoria", "Categoría"), ("archivo", "Archivo")],
        "campos": [
            campo("titulo", "Título", requerido=True),
            campo("categoria", "Categoría", "opciones", opciones=CATEGORIAS_DOCUMENTO,
                  requerido=True),
            campo("descripcion", "Descripción", "textarea"),
            campo("archivo", "Archivo (PDF, Word, imagen)", "archivo"),
            campo("enlace", "O un enlace externo (opcional)"),
        ],
        "extra_nuevo": {"fecha_publicacion": lambda: datetime.now().isoformat(timespec="seconds")},
    },
    "bloques": {
        "titulo": "Actividades fijas de la sede",
        "singular": "actividad fija",
        "tabla": "bloques_fijos",
        "orden": "dia_semana, hora_inicio",
        "columnas": [("dia_semana", "Día"), ("hora_inicio", "Desde"), ("hora_fin", "Hasta"),
                     ("actividad", "Actividad"), ("activo", "Activa")],
        "campos": [
            campo("actividad", "Actividad", requerido=True),
            campo("dia_semana", "Día de la semana", "opciones",
                  opciones=list(enumerate(DIAS_SEMANA)), requerido=True, valores_numericos=True),
            campo("hora_inicio", "Hora de inicio", "hora", requerido=True),
            campo("hora_fin", "Hora de término", "hora", requerido=True),
            campo("responsable", "Responsable"),
            campo("activo", "Activa", "casilla", defecto=1),
        ],
    },
    "servicios": {
        "titulo": "Directorio de servicios",
        "singular": "servicio",
        "tabla": "servicios",
        "orden": "aprobado, nombre_servicio",
        "columnas": [("nombre_servicio", "Servicio"), ("vecino", "Vecino/a"), ("rubro", "Rubro"),
                     ("aprobado", "Publicado")],
        "campos": [
            campo("nombre_servicio", "Nombre del servicio", requerido=True),
            campo("vecino", "Vecino o vecina responsable", requerido=True),
            campo("rubro", "Rubro", "opciones", opciones=RUBROS_SERVICIO, requerido=True),
            campo("descripcion", "Descripción", "textarea"),
            campo("telefono", "Teléfono"),
            campo("whatsapp", "WhatsApp (solo números, con 56)"),
            campo("email", "Correo"),
            campo("direccion", "Dirección"),
            campo("aprobado", "Publicado en el directorio", "casilla"),
        ],
        "extra_nuevo": {"fecha_solicitud": lambda: datetime.now().isoformat(timespec="seconds")},
    },
    "directiva": {
        "titulo": "Directiva",
        "singular": "integrante de la directiva",
        "tabla": "directiva",
        "orden": "orden",
        "columnas": [("orden", "Orden"), ("nombre", "Nombre"), ("cargo", "Cargo"),
                     ("email", "Correo")],
        "campos": [
            campo("nombre", "Nombre", requerido=True),
            campo("cargo", "Cargo", requerido=True),
            campo("email", "Correo del cargo"),
            campo("telefono", "Teléfono"),
            campo("foto_url", "Enlace a la foto"),
            campo("orden", "Orden en la página", "numero", defecto=0),
        ],
    },
    "eventos": {
        "titulo": "Actividades con balance",
        "singular": "actividad",
        "tabla": "eventos",
        "orden": "fecha DESC",
        "columnas": [("nombre", "Actividad"), ("fecha", "Fecha"), ("publicado", "Publicada")],
        "campos": [
            campo("nombre", "Nombre de la actividad", requerido=True),
            campo("fecha", "Fecha", "fecha", requerido=True),
            campo("descripcion", "Descripción", "textarea"),
            campo("publicado", "Publicada en transparencia", "casilla", defecto=1),
        ],
        "movimientos": "evento_id",
    },
    "rendiciones": {
        "titulo": "Rendiciones de cuentas",
        "singular": "rendición",
        "tabla": "rendiciones",
        "orden": "fecha_publicacion DESC",
        "columnas": [("titulo", "Título"), ("periodo", "Período")],
        "campos": [
            campo("titulo", "Título", requerido=True),
            campo("periodo", "Período", requerido=True, ayuda="Por ejemplo: 2025 o Marzo 2026."),
            campo("descripcion", "Descripción", "textarea"),
            campo("archivo", "Documento respaldo", "archivo"),
        ],
        "extra_nuevo": {"fecha_publicacion": lambda: datetime.now().isoformat(timespec="seconds")},
        "movimientos": "rendicion_id",
    },
    "usuarios": {
        "titulo": "Usuarios del sistema",
        "singular": "usuario",
        "tabla": "usuarios",
        "orden": "rol, nombre",
        "columnas": [("nombre", "Nombre"), ("usuario", "Usuario"), ("rol", "Perfil"),
                     ("activo", "Activo")],
        "campos": [
            campo("nombre", "Nombre completo", requerido=True),
            campo("usuario", "Nombre de usuario", requerido=True),
            campo("email", "Correo"),
            campo("rol", "Perfil", "opciones", opciones=ROLES, requerido=True),
            campo("activo", "Activo", "casilla", defecto=1),
            campo("clave", "Clave", "clave",
                  ayuda="Déjala en blanco para mantener la clave actual."),
        ],
        "extra_nuevo": {"fecha_creacion": lambda: datetime.now().isoformat(timespec="seconds")},
        "solo_admin": True,
    },
}

PERMISOS_COORDINADOR = {
    "panel", "noticias", "documentos", "bloques", "servicios", "eventos",
    "reservas", "certificados", "mensajes",
}

MENU_PANEL = [
    ("reservas", "Reservas de la sede", "🗓️"),
    ("certificados", "Certificados de residencia", "📄"),
    ("mensajes", "Mensajes de vecinos", "✉️"),
    ("noticias", "Noticias y actividades", "📢"),
    ("bloques", "Actividades fijas de la sede", "🏛️"),
    ("servicios", "Directorio de servicios", "🧰"),
    ("documentos", "Documentos", "📚"),
    ("proyectos", "Proyectos", "🏗️"),
    ("rendiciones", "Rendiciones de cuentas", "💰"),
    ("eventos", "Actividades con balance", "🎉"),
    ("directiva", "Directiva", "👥"),
    ("contenido", "Textos del sitio", "⚙️"),
    ("usuarios", "Usuarios del sistema", "🔐"),
]


ENDPOINTS_ESPECIALES = {
    "reservas": "panel_reservas",
    "certificados": "panel_certificados",
    "mensajes": "panel_mensajes",
    "contenido": "panel_contenido",
}


def url_panel(clave):
    if clave in ENDPOINTS_ESPECIALES:
        return url_for(ENDPOINTS_ESPECIALES[clave])
    return url_for("panel_listado", recurso=clave)


# ---------------------------------------------------------------------------
# Contexto común para las plantillas
# ---------------------------------------------------------------------------
@app.context_processor
def contexto_global():
    conf = obtener_config()
    usuario = usuario_actual()
    pendientes = 0
    if usuario and usuario["rol"] in ("administrador", "coordinador"):
        pendientes = (
            consultar("SELECT COUNT(*) c FROM reservas WHERE estado = 'Pendiente'", uno=True)["c"]
            + consultar("SELECT COUNT(*) c FROM certificados WHERE estado = 'Recibida'", uno=True)["c"]
            + consultar("SELECT COUNT(*) c FROM mensajes WHERE leido = 0", uno=True)["c"]
            + consultar("SELECT COUNT(*) c FROM servicios WHERE aprobado = 0", uno=True)["c"]
        )
    return {
        "conf": conf,
        "usuario": usuario,
        "puede": puede,
        "url_panel": url_panel,
        "anio_actual": date.today().year,
        "menu_panel": MENU_PANEL,
        "pendientes_panel": pendientes,
    }


# ---------------------------------------------------------------------------
# Sitio público
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    noticias = consultar(
        "SELECT * FROM noticias WHERE publicado = 1 ORDER BY fecha_publicacion DESC LIMIT 3"
    )
    proyectos = consultar(
        "SELECT * FROM proyectos WHERE horizonte = 'Actual' "
        "ORDER BY fecha_actualizacion DESC LIMIT 3"
    )
    semana = semana_de(date.today())
    return render_template(
        "index.html", noticias=noticias, proyectos=proyectos, semana=semana
    )


@app.route("/noticias")
def noticias():
    lista = consultar(
        "SELECT * FROM noticias WHERE publicado = 1 ORDER BY fecha_publicacion DESC"
    )
    return render_template("noticias.html", noticias=lista)


@app.route("/noticias/<int:noticia_id>")
def noticia_detalle(noticia_id):
    noticia = consultar(
        "SELECT * FROM noticias WHERE id = ? AND publicado = 1", (noticia_id,), uno=True
    )
    if not noticia:
        abort(404)
    return render_template("noticia_detalle.html", noticia=noticia)


@app.route("/proyectos")
def proyectos():
    estado = request.args.get("estado") or ""
    sql = "SELECT * FROM proyectos WHERE horizonte = 'Actual'"
    params = []
    if estado in ESTADOS_PROYECTO:
        sql += " AND estado = ?"
        params.append(estado)
    sql += " ORDER BY fecha_actualizacion DESC"
    return render_template(
        "proyectos.html",
        proyectos=consultar(sql, params),
        estados=ESTADOS_PROYECTO,
        filtro_estado=estado,
    )


@app.route("/plan-maestro")
def plan_maestro():
    actuales = consultar(
        "SELECT * FROM proyectos WHERE horizonte = 'Actual' ORDER BY eje, nombre"
    )
    futuros = consultar(
        "SELECT * FROM proyectos WHERE horizonte = 'Futuro' ORDER BY eje, nombre"
    )
    ejes = sorted({(p["eje"] or "General") for p in list(actuales) + list(futuros)})
    return render_template(
        "plan_maestro.html", actuales=actuales, futuros=futuros, ejes=ejes
    )


def totales_de(movimientos):
    ingresos = sum(m["monto"] for m in movimientos if m["tipo"] == "Ingreso")
    gastos = sum(m["monto"] for m in movimientos if m["tipo"] == "Gasto")
    return {"ingresos": ingresos, "gastos": gastos, "saldo": ingresos - gastos}


@app.route("/transparencia")
def transparencia():
    rendiciones = []
    for r in consultar("SELECT * FROM rendiciones ORDER BY fecha_publicacion DESC"):
        movs = consultar(
            "SELECT * FROM movimientos WHERE rendicion_id = ? ORDER BY fecha", (r["id"],)
        )
        rendiciones.append({"datos": r, "movimientos": movs, "totales": totales_de(movs)})

    eventos = []
    for e in consultar("SELECT * FROM eventos WHERE publicado = 1 ORDER BY fecha DESC"):
        movs = consultar(
            "SELECT * FROM movimientos WHERE evento_id = ? ORDER BY fecha", (e["id"],)
        )
        eventos.append({"datos": e, "movimientos": movs, "totales": totales_de(movs)})

    documentos = consultar(
        "SELECT * FROM documentos WHERE categoria = 'Rendiciones de cuentas' "
        "ORDER BY fecha_publicacion DESC"
    )
    return render_template(
        "transparencia.html", rendiciones=rendiciones, eventos=eventos, documentos=documentos
    )


@app.route("/documentos")
def documentos():
    categoria = request.args.get("categoria") or ""
    sql = "SELECT * FROM documentos"
    params = []
    if categoria in CATEGORIAS_DOCUMENTO:
        sql += " WHERE categoria = ?"
        params.append(categoria)
    sql += " ORDER BY categoria, fecha_publicacion DESC"
    return render_template(
        "documentos.html",
        documentos=consultar(sql, params),
        categorias=CATEGORIAS_DOCUMENTO,
        filtro_categoria=categoria,
    )


@app.route("/archivos/<path:nombre>")
def archivo(nombre):
    return send_from_directory(CARPETA_SUBIDAS, nombre)


# ---------------------------------------------------------------------------
# Sede vecinal: calendario y reservas
# ---------------------------------------------------------------------------
def semana_de(dia_referencia):
    """Devuelve la lista de 7 días (lunes a domingo) con su ocupación."""
    lunes = dia_referencia - timedelta(days=dia_referencia.weekday())
    bloques = consultar("SELECT * FROM bloques_fijos WHERE activo = 1 ORDER BY hora_inicio")
    domingo = lunes + timedelta(days=6)
    reservas = consultar(
        "SELECT * FROM reservas WHERE fecha BETWEEN ? AND ? AND estado IN "
        "('Aprobada', 'Pendiente') ORDER BY hora_inicio",
        (lunes.isoformat(), domingo.isoformat()),
    )
    dias = []
    for i in range(7):
        dia = lunes + timedelta(days=i)
        actividades = [
            {
                "tipo": "fija",
                "hora_inicio": b["hora_inicio"],
                "hora_fin": b["hora_fin"],
                "titulo": b["actividad"],
                "detalle": b["responsable"],
                "estado": "Fija",
            }
            for b in bloques
            if b["dia_semana"] == i
        ]
        actividades += [
            {
                "tipo": "reserva",
                "hora_inicio": r["hora_inicio"],
                "hora_fin": r["hora_fin"],
                "titulo": r["actividad"],
                "detalle": r["solicitante"],
                "estado": r["estado"],
            }
            for r in reservas
            if r["fecha"] == dia.isoformat()
        ]
        actividades.sort(key=lambda a: a["hora_inicio"])
        dias.append(
            {
                "fecha": dia,
                "nombre": DIAS_SEMANA[i],
                "actividades": actividades,
                "libre": not actividades,
                "pasado": dia < date.today(),
                "hoy": dia == date.today(),
            }
        )
    return {"lunes": lunes, "domingo": domingo, "dias": dias}


@app.route("/sede")
def sede():
    referencia = a_fecha(request.args.get("semana")) or date.today()
    semana = semana_de(referencia)
    return render_template(
        "sede.html",
        semana=semana,
        anterior=(semana["lunes"] - timedelta(days=7)).isoformat(),
        siguiente=(semana["lunes"] + timedelta(days=7)).isoformat(),
        bloques=consultar(
            "SELECT * FROM bloques_fijos WHERE activo = 1 ORDER BY dia_semana, hora_inicio"
        ),
        dias_semana=DIAS_SEMANA,
    )


@app.route("/sede/reservar", methods=["GET", "POST"])
def reservar():
    usuario = usuario_actual()
    datos = {
        "fecha": request.args.get("fecha", ""),
        "solicitante": usuario["nombre"] if usuario else "",
        "email": (usuario["email"] if usuario else "") or "",
    }
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip() for k in
                 ("fecha", "hora_inicio", "hora_fin", "actividad", "solicitante",
                  "telefono", "email", "personas", "observacion")}
        faltan = [c for c in ("fecha", "hora_inicio", "hora_fin", "actividad",
                              "solicitante", "telefono") if not datos[c]]
        fecha = a_fecha(datos["fecha"])
        if faltan:
            flash("Completa los datos obligatorios del formulario.", "error")
        elif not fecha:
            flash("La fecha no es válida.", "error")
        elif fecha < date.today():
            flash("No se puede reservar una fecha que ya pasó.", "error")
        elif datos["hora_fin"] <= datos["hora_inicio"]:
            flash("La hora de término debe ser posterior a la de inicio.", "error")
        elif hay_choque(fecha, datos["hora_inicio"], datos["hora_fin"]):
            flash(
                "Ese horario ya está ocupado en la sede. Revisa el calendario y elige otro.",
                "error",
            )
        else:
            ejecutar(
                "INSERT INTO reservas (fecha, hora_inicio, hora_fin, actividad, solicitante, "
                "telefono, email, personas, estado, observacion, revisada_por, fecha_solicitud) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pendiente', ?, '', ?)",
                (
                    fecha.isoformat(), datos["hora_inicio"], datos["hora_fin"],
                    datos["actividad"], datos["solicitante"], datos["telefono"],
                    datos["email"], int(datos["personas"] or 0) or None,
                    datos["observacion"],
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            flash(
                "Tu solicitud de reserva fue enviada. El encargado o encargada de la sede la "
                "revisará y te contactará para confirmarla.",
                "success",
            )
            return redirect(url_for("sede", semana=fecha.isoformat()))
    return render_template("reservar.html", datos=datos)


def hay_choque(fecha, inicio, fin, excluir_id=None):
    """True si el horario pedido se cruza con una actividad fija o una reserva vigente."""
    for b in consultar(
        "SELECT * FROM bloques_fijos WHERE activo = 1 AND dia_semana = ?", (fecha.weekday(),)
    ):
        if inicio < b["hora_fin"] and fin > b["hora_inicio"]:
            return True
    sql = ("SELECT * FROM reservas WHERE fecha = ? AND estado IN ('Aprobada', 'Pendiente')")
    params = [fecha.isoformat()]
    if excluir_id:
        sql += " AND id != ?"
        params.append(excluir_id)
    for r in consultar(sql, params):
        if inicio < r["hora_fin"] and fin > r["hora_inicio"]:
            return True
    return False


# ---------------------------------------------------------------------------
# Directorio de servicios
# ---------------------------------------------------------------------------
@app.route("/directorio")
def directorio():
    rubro = request.args.get("rubro") or ""
    busqueda = (request.args.get("q") or "").strip()
    sql = "SELECT * FROM servicios WHERE aprobado = 1"
    params = []
    if rubro in RUBROS_SERVICIO:
        sql += " AND rubro = ?"
        params.append(rubro)
    sql += " ORDER BY rubro, nombre_servicio"
    lista = consultar(sql, params)
    if busqueda:
        clave = sin_acentos(busqueda)
        lista = [
            s for s in lista
            if clave in sin_acentos(s["nombre_servicio"])
            or clave in sin_acentos(s["descripcion"])
            or clave in sin_acentos(s["vecino"])
        ]
    return render_template(
        "directorio.html", servicios=lista, rubros=RUBROS_SERVICIO,
        filtro_rubro=rubro, busqueda=busqueda,
    )


@app.route("/directorio/inscribir", methods=["GET", "POST"])
def inscribir_servicio():
    datos = {}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip() for k in
                 ("nombre_servicio", "vecino", "rubro", "descripcion", "telefono",
                  "whatsapp", "email", "direccion")}
        if not datos["nombre_servicio"] or not datos["vecino"] or not datos["telefono"]:
            flash("Completa el nombre del servicio, tu nombre y un teléfono.", "error")
        else:
            ejecutar(
                "INSERT INTO servicios (nombre_servicio, vecino, rubro, descripcion, telefono, "
                "whatsapp, email, direccion, aprobado, fecha_solicitud) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    datos["nombre_servicio"], datos["vecino"],
                    datos["rubro"] if datos["rubro"] in RUBROS_SERVICIO else "Otros",
                    datos["descripcion"], datos["telefono"],
                    solo_digitos(datos["whatsapp"]), datos["email"], datos["direccion"],
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            flash(
                "¡Gracias! Tu servicio quedó registrado y aparecerá en el directorio una vez "
                "que la directiva lo revise.",
                "success",
            )
            return redirect(url_for("directorio"))
    return render_template("inscribir_servicio.html", rubros=RUBROS_SERVICIO, datos=datos)


# ---------------------------------------------------------------------------
# Certificado de residencia
# ---------------------------------------------------------------------------
@app.route("/certificado-residencia", methods=["GET", "POST"])
def certificado():
    usuario = usuario_actual()
    datos = {"nombre": usuario["nombre"] if usuario else "",
             "email": (usuario["email"] if usuario else "") or ""}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip() for k in
                 ("nombre", "rut", "direccion", "telefono", "email", "motivo")}
        if not datos["nombre"] or not datos["rut"] or not datos["direccion"]:
            flash("Completa tu nombre, RUT y dirección.", "error")
        else:
            folio = ejecutar(
                "INSERT INTO certificados (nombre, rut, direccion, telefono, email, motivo, "
                "estado, observacion, fecha_solicitud) VALUES (?, ?, ?, ?, ?, ?, 'Recibida', '', ?)",
                (
                    datos["nombre"], datos["rut"], datos["direccion"], datos["telefono"],
                    datos["email"], datos["motivo"],
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            flash(
                f"Solicitud registrada con el número {folio}. Anótalo: con ese número puedes "
                "consultar el estado de tu certificado.",
                "success",
            )
            return redirect(url_for("estado_certificado", folio=folio))
    requisitos = [
        r.strip() for r in obtener_config()["requisitos_certificado"].split("\n") if r.strip()
    ]
    return render_template("certificado.html", datos=datos, requisitos=requisitos)


@app.route("/certificado-residencia/estado")
def estado_certificado():
    folio = request.args.get("folio", type=int)
    solicitud = None
    if folio:
        solicitud = consultar("SELECT * FROM certificados WHERE id = ?", (folio,), uno=True)
        if not solicitud:
            flash("No encontramos una solicitud con ese número.", "error")
    return render_template("estado_certificado.html", solicitud=solicitud, folio=folio)


# ---------------------------------------------------------------------------
# Quiénes somos y contacto
# ---------------------------------------------------------------------------
@app.route("/quienes-somos")
def quienes_somos():
    miembros = consultar("SELECT * FROM directiva ORDER BY orden, nombre")
    return render_template("quienes_somos.html", miembros=miembros)


@app.route("/contacto", methods=["GET", "POST"])
def contacto():
    datos = {}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip()
                 for k in ("nombre", "email", "telefono", "mensaje")}
        if not datos["nombre"] or not datos["email"] or not datos["mensaje"]:
            flash("Por favor completa tu nombre, correo y mensaje.", "error")
        else:
            ejecutar(
                "INSERT INTO mensajes (nombre, email, telefono, mensaje, leido, fecha_envio) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (datos["nombre"], datos["email"], datos["telefono"], datos["mensaje"],
                 datetime.now().isoformat(timespec="seconds")),
            )
            flash("¡Gracias! Tu mensaje fue enviado a la directiva.", "success")
            return redirect(url_for("contacto"))
    miembros = consultar("SELECT * FROM directiva WHERE email != '' ORDER BY orden")
    return render_template("contacto.html", datos=datos, miembros=miembros)


# ---------------------------------------------------------------------------
# Sesión
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nombre_usuario = (request.form.get("usuario") or "").strip()
        clave = request.form.get("clave") or ""
        fila = consultar(
            "SELECT * FROM usuarios WHERE usuario = ? AND activo = 1", (nombre_usuario,), uno=True
        )
        if fila and check_password_hash(fila["clave_hash"], clave):
            session["usuario_id"] = fila["id"]
            destino = request.args.get("next")
            if not destino or not destino.startswith("/"):
                destino = url_for("panel") if fila["rol"] != "vecino" else url_for("mis_solicitudes")
            return redirect(destino)
        flash("Usuario o clave incorrectos.", "error")
    return render_template("login.html")


@app.route("/registro", methods=["GET", "POST"])
def registro():
    datos = {}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip()
                 for k in ("nombre", "usuario", "email")}
        clave = request.form.get("clave") or ""
        if not datos["nombre"] or not datos["usuario"] or len(clave) < 6:
            flash("Completa tu nombre, un usuario y una clave de al menos 6 caracteres.", "error")
        elif consultar("SELECT id FROM usuarios WHERE usuario = ?", (datos["usuario"],), uno=True):
            flash("Ese nombre de usuario ya existe, elige otro.", "error")
        else:
            uid = ejecutar(
                "INSERT INTO usuarios (nombre, usuario, email, clave_hash, rol, activo, "
                "fecha_creacion) VALUES (?, ?, ?, ?, 'vecino', 1, ?)",
                (datos["nombre"], datos["usuario"], datos["email"],
                 generate_password_hash(clave),
                 datetime.now().isoformat(timespec="seconds")),
            )
            session["usuario_id"] = uid
            flash("¡Bienvenido/a! Tu cuenta de vecino/a fue creada.", "success")
            return redirect(url_for("mis_solicitudes"))
    return render_template("registro.html", datos=datos)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/mis-solicitudes")
@login_requerido
def mis_solicitudes():
    usuario = usuario_actual()
    email = usuario["email"] or ""
    reservas = consultar(
        "SELECT * FROM reservas WHERE email != '' AND email = ? ORDER BY fecha DESC", (email,)
    ) if email else []
    certificados = consultar(
        "SELECT * FROM certificados WHERE email != '' AND email = ? ORDER BY id DESC", (email,)
    ) if email else []
    return render_template(
        "mis_solicitudes.html", reservas=reservas, certificados=certificados
    )


# ---------------------------------------------------------------------------
# Panel de administración
# ---------------------------------------------------------------------------
@app.route("/panel")
@login_requerido
def panel():
    usuario = usuario_actual()
    if usuario["rol"] == "vecino":
        return redirect(url_for("mis_solicitudes"))
    resumen = {
        "reservas_pendientes": consultar(
            "SELECT COUNT(*) c FROM reservas WHERE estado = 'Pendiente'", uno=True)["c"],
        "certificados_pendientes": consultar(
            "SELECT COUNT(*) c FROM certificados WHERE estado IN ('Recibida', 'En revisión')",
            uno=True)["c"],
        "mensajes_nuevos": consultar(
            "SELECT COUNT(*) c FROM mensajes WHERE leido = 0", uno=True)["c"],
        "servicios_por_aprobar": consultar(
            "SELECT COUNT(*) c FROM servicios WHERE aprobado = 0", uno=True)["c"],
        "noticias": consultar("SELECT COUNT(*) c FROM noticias", uno=True)["c"],
        "proyectos": consultar("SELECT COUNT(*) c FROM proyectos", uno=True)["c"],
    }
    proximas = consultar(
        "SELECT * FROM reservas WHERE fecha >= ? AND estado = 'Aprobada' "
        "ORDER BY fecha LIMIT 5", (date.today().isoformat(),)
    )
    return render_template("panel/inicio.html", resumen=resumen, proximas=proximas)


# --- Reservas -------------------------------------------------------------
@app.route("/panel/reservas")
@permiso_requerido("reservas")
def panel_reservas():
    estado = request.args.get("estado") or "Pendiente"
    sql = "SELECT * FROM reservas"
    params = []
    if estado in ESTADOS_RESERVA:
        sql += " WHERE estado = ?"
        params.append(estado)
    sql += " ORDER BY fecha DESC, hora_inicio"
    return render_template(
        "panel/reservas.html", reservas=consultar(sql, params),
        estados=ESTADOS_RESERVA, filtro_estado=estado,
    )


@app.route("/panel/reservas/<int:reserva_id>/estado", methods=["POST"])
@permiso_requerido("reservas")
def panel_reserva_estado(reserva_id):
    nuevo = request.form.get("estado")
    observacion = (request.form.get("observacion") or "").strip()
    if nuevo not in ESTADOS_RESERVA:
        abort(400)
    usuario = usuario_actual()
    ejecutar(
        "UPDATE reservas SET estado = ?, observacion = ?, revisada_por = ? WHERE id = ?",
        (nuevo, observacion, usuario["nombre"], reserva_id),
    )
    flash(f"La reserva quedó marcada como «{nuevo}».", "success")
    return redirect(request.referrer or url_for("panel_reservas"))


@app.route("/panel/reservas/nueva", methods=["GET", "POST"])
@permiso_requerido("reservas")
def panel_reserva_nueva():
    """El encargado bloquea directamente un día ocupado."""
    datos = {"fecha": request.args.get("fecha", "")}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip() for k in
                 ("fecha", "hora_inicio", "hora_fin", "actividad", "solicitante",
                  "telefono", "observacion")}
        fecha = a_fecha(datos["fecha"])
        if not fecha or not datos["hora_inicio"] or not datos["hora_fin"] or not datos["actividad"]:
            flash("Completa fecha, horario y actividad.", "error")
        else:
            usuario = usuario_actual()
            ejecutar(
                "INSERT INTO reservas (fecha, hora_inicio, hora_fin, actividad, solicitante, "
                "telefono, email, personas, estado, observacion, revisada_por, fecha_solicitud) "
                "VALUES (?, ?, ?, ?, ?, ?, '', NULL, 'Aprobada', ?, ?, ?)",
                (fecha.isoformat(), datos["hora_inicio"], datos["hora_fin"], datos["actividad"],
                 datos["solicitante"] or "Junta de Vecinos", datos["telefono"],
                 datos["observacion"], usuario["nombre"],
                 datetime.now().isoformat(timespec="seconds")),
            )
            flash("La sede quedó marcada como ocupada en ese horario.", "success")
            return redirect(url_for("panel_reservas", estado="Aprobada"))
    return render_template("panel/reserva_nueva.html", datos=datos)


# --- Certificados ---------------------------------------------------------
@app.route("/panel/certificados")
@permiso_requerido("certificados")
def panel_certificados():
    estado = request.args.get("estado") or ""
    sql = "SELECT * FROM certificados"
    params = []
    if estado in ESTADOS_CERTIFICADO:
        sql += " WHERE estado = ?"
        params.append(estado)
    sql += " ORDER BY id DESC"
    return render_template(
        "panel/certificados.html", solicitudes=consultar(sql, params),
        estados=ESTADOS_CERTIFICADO, filtro_estado=estado,
    )


@app.route("/panel/certificados/<int:solicitud_id>/estado", methods=["POST"])
@permiso_requerido("certificados")
def panel_certificado_estado(solicitud_id):
    nuevo = request.form.get("estado")
    if nuevo not in ESTADOS_CERTIFICADO:
        abort(400)
    ejecutar(
        "UPDATE certificados SET estado = ?, observacion = ? WHERE id = ?",
        (nuevo, (request.form.get("observacion") or "").strip(), solicitud_id),
    )
    flash("Solicitud actualizada.", "success")
    return redirect(request.referrer or url_for("panel_certificados"))


# --- Mensajes -------------------------------------------------------------
@app.route("/panel/mensajes")
@permiso_requerido("mensajes")
def panel_mensajes():
    return render_template(
        "panel/mensajes.html",
        mensajes=consultar("SELECT * FROM mensajes ORDER BY leido, fecha_envio DESC"),
    )


@app.route("/panel/mensajes/<int:mensaje_id>/leido", methods=["POST"])
@permiso_requerido("mensajes")
def panel_mensaje_leido(mensaje_id):
    ejecutar("UPDATE mensajes SET leido = 1 WHERE id = ?", (mensaje_id,))
    return redirect(url_for("panel_mensajes"))


# --- Textos del sitio -----------------------------------------------------
CAMPOS_CONTENIDO = [
    ("socios_inscritos", "Socias y socios inscritos", "texto"),
    ("mision", "Misión", "textarea"),
    ("vision", "Visión", "textarea"),
    ("historia", "Reseña de la organización", "textarea"),
    ("direccion_sede", "Dirección de la sede", "texto"),
    ("horario_atencion", "Horario de atención", "texto"),
    ("email_contacto", "Correo de contacto", "texto"),
    ("telefono_contacto", "Teléfono de contacto", "texto"),
    ("whatsapp_grupo", "Enlace al grupo de WhatsApp", "texto"),
    ("facebook", "Enlace a Facebook", "texto"),
    ("instagram", "Enlace a Instagram", "texto"),
    ("plan_maestro_intro", "Introducción del plan maestro", "textarea"),
    ("requisitos_certificado", "Requisitos del certificado (uno por línea)", "textarea"),
]


@app.route("/panel/contenido", methods=["GET", "POST"])
@permiso_requerido("contenido")
def panel_contenido():
    if request.method == "POST":
        for clave, _, _ in CAMPOS_CONTENIDO:
            base.guardar_config(clave, (request.form.get(clave) or "").strip())
        flash("Los textos del sitio fueron actualizados.", "success")
        return redirect(url_for("panel_contenido"))
    return render_template("panel/contenido.html", campos=CAMPOS_CONTENIDO)


# --- CRUD genérico --------------------------------------------------------
def recurso_o_404(nombre):
    recurso = RECURSOS.get(nombre)
    if not recurso:
        abort(404)
    return recurso


def valores_desde_formulario(recurso, fila_actual=None):
    valores = {}
    for c in recurso["campos"]:
        nombre = c["nombre"]
        if c["tipo"] == "casilla":
            valores[nombre] = 1 if request.form.get(nombre) else 0
        elif c["tipo"] == "archivo":
            guardado = guardar_archivo(nombre)
            if guardado:
                valores[nombre] = guardado
            elif fila_actual is not None:
                valores[nombre] = fila_actual[nombre]
            else:
                valores[nombre] = ""
        elif c["tipo"] == "clave":
            clave = request.form.get(nombre) or ""
            if clave:
                valores["clave_hash"] = generate_password_hash(clave)
        elif c["tipo"] == "numero" or c.get("valores_numericos"):
            crudo = (request.form.get(nombre) or "").strip()
            valores[nombre] = int(crudo) if crudo.lstrip("-").isdigit() else None
        else:
            valores[nombre] = (request.form.get(nombre) or "").strip()
    return valores


@app.route("/panel/<recurso>")
@login_requerido
def panel_listado(recurso):
    definicion = recurso_o_404(recurso)
    if not puede(recurso):
        flash("Tu perfil no tiene permiso para esta sección.", "error")
        return redirect(url_for("panel"))
    filas = consultar(f"SELECT * FROM {definicion['tabla']} ORDER BY {definicion['orden']}")
    return render_template(
        "panel/listado.html", recurso=recurso, definicion=definicion, filas=filas,
        dias_semana=DIAS_SEMANA,
    )


@app.route("/panel/<recurso>/nuevo", methods=["GET", "POST"])
@login_requerido
def panel_nuevo(recurso):
    definicion = recurso_o_404(recurso)
    if not puede(recurso):
        flash("Tu perfil no tiene permiso para esta sección.", "error")
        return redirect(url_for("panel"))
    if request.method == "POST":
        valores = valores_desde_formulario(definicion)
        for clave, generador in definicion.get("extra_nuevo", {}).items():
            valores[clave] = generador()
        for clave, generador in definicion.get("extra_siempre", {}).items():
            valores[clave] = generador()
        if recurso == "usuarios":
            if "clave_hash" not in valores:
                flash("Debes escribir una clave para el nuevo usuario.", "error")
                return render_template(
                    "panel/formulario.html", recurso=recurso, definicion=definicion, fila=None)
            valores.pop("clave", None)
        columnas = ", ".join(valores.keys())
        marcas = ", ".join("?" for _ in valores)
        ejecutar(
            f"INSERT INTO {definicion['tabla']} ({columnas}) VALUES ({marcas})",
            tuple(valores.values()),
        )
        flash(f"Se agregó {definicion['singular']}.", "success")
        return redirect(url_for("panel_listado", recurso=recurso))
    return render_template(
        "panel/formulario.html", recurso=recurso, definicion=definicion, fila=None
    )


@app.route("/panel/<recurso>/<int:fila_id>/editar", methods=["GET", "POST"])
@login_requerido
def panel_editar(recurso, fila_id):
    definicion = recurso_o_404(recurso)
    if not puede(recurso):
        flash("Tu perfil no tiene permiso para esta sección.", "error")
        return redirect(url_for("panel"))
    fila = consultar(
        f"SELECT * FROM {definicion['tabla']} WHERE id = ?", (fila_id,), uno=True
    )
    if not fila:
        abort(404)
    if request.method == "POST":
        valores = valores_desde_formulario(definicion, fila)
        valores.pop("clave", None)
        for clave, generador in definicion.get("extra_siempre", {}).items():
            valores[clave] = generador()
        asignaciones = ", ".join(f"{k} = ?" for k in valores)
        ejecutar(
            f"UPDATE {definicion['tabla']} SET {asignaciones} WHERE id = ?",
            tuple(valores.values()) + (fila_id,),
        )
        flash("Los cambios fueron guardados.", "success")
        return redirect(url_for("panel_listado", recurso=recurso))
    return render_template(
        "panel/formulario.html", recurso=recurso, definicion=definicion, fila=fila
    )


@app.route("/panel/<recurso>/<int:fila_id>/eliminar", methods=["POST"])
@login_requerido
def panel_eliminar(recurso, fila_id):
    definicion = recurso_o_404(recurso)
    if not puede(recurso):
        flash("Tu perfil no tiene permiso para esta sección.", "error")
        return redirect(url_for("panel"))
    usuario = usuario_actual()
    if recurso == "usuarios" and fila_id == usuario["id"]:
        flash("No puedes eliminar tu propio usuario.", "error")
        return redirect(url_for("panel_listado", recurso=recurso))
    ejecutar(f"DELETE FROM {definicion['tabla']} WHERE id = ?", (fila_id,))
    flash("Registro eliminado.", "success")
    return redirect(url_for("panel_listado", recurso=recurso))


# --- Movimientos de rendiciones y actividades ------------------------------
@app.route("/panel/<recurso>/<int:fila_id>/movimientos", methods=["GET", "POST"])
@login_requerido
def panel_movimientos(recurso, fila_id):
    definicion = recurso_o_404(recurso)
    columna = definicion.get("movimientos")
    if not columna or not puede(recurso):
        abort(404)
    fila = consultar(
        f"SELECT * FROM {definicion['tabla']} WHERE id = ?", (fila_id,), uno=True
    )
    if not fila:
        abort(404)
    if request.method == "POST":
        concepto = (request.form.get("concepto") or "").strip()
        tipo = request.form.get("tipo")
        monto = (request.form.get("monto") or "").strip()
        fecha = a_fecha(request.form.get("fecha")) or date.today()
        if not concepto or tipo not in ("Ingreso", "Gasto") or not monto.isdigit():
            flash("Completa el concepto, el tipo y un monto en números.", "error")
        else:
            ejecutar(
                f"INSERT INTO movimientos ({columna}, fecha, concepto, tipo, monto) "
                "VALUES (?, ?, ?, ?, ?)",
                (fila_id, fecha.isoformat(), concepto, tipo, int(monto)),
            )
            flash("Movimiento agregado.", "success")
        return redirect(url_for("panel_movimientos", recurso=recurso, fila_id=fila_id))
    movimientos = consultar(
        f"SELECT * FROM movimientos WHERE {columna} = ? ORDER BY fecha", (fila_id,)
    )
    return render_template(
        "panel/movimientos.html", recurso=recurso, definicion=definicion, fila=fila,
        movimientos=movimientos, totales=totales_de(movimientos),
    )


@app.route("/panel/movimientos/<int:movimiento_id>/eliminar", methods=["POST"])
@login_requerido
def panel_movimiento_eliminar(movimiento_id):
    if not (puede("rendiciones") or puede("eventos")):
        abort(403)
    ejecutar("DELETE FROM movimientos WHERE id = ?", (movimiento_id,))
    flash("Movimiento eliminado.", "success")
    return redirect(request.referrer or url_for("panel"))


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------
@app.errorhandler(404)
def no_encontrado(e):
    return render_template("error.html", codigo=404,
                           mensaje="No encontramos la página que buscas."), 404


@app.errorhandler(413)
def archivo_muy_grande(e):
    return render_template("error.html", codigo=413,
                           mensaje="El archivo que intentaste subir es demasiado grande "
                                   "(máximo 16 MB)."), 413


@app.errorhandler(500)
def error_interno(e):  # pragma: no cover
    return render_template("error.html", codigo=500,
                           mensaje="Ocurrió un error inesperado. Intenta nuevamente."), 500


base.init_db(app)


if __name__ == "__main__":
    # En macOS el puerto 5000 lo ocupa el "Receptor AirPlay", por eso se usa 5001
    # por defecto. Se puede cambiar con la variable de entorno PORT.
    puerto = int(os.environ.get("PORT", "5001"))
    app.run(debug=True, host="0.0.0.0", port=puerto)
