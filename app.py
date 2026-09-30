"""Sitio web de la Junta de Vecinos N.° 2 · Cerro Esperanza.

Aplicación Flask con SQLite. Incluye el sitio público (noticias, proyectos,
transparencia, documentos, sede vecinal con reservas, directorio de servicios y
certificado de residencia) y un panel de administración en español con tres
perfiles: administrador, coordinador y vecino.
"""

import os
import calendar
import secrets
import sqlite3
import shutil
import unicodedata
from datetime import date, datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

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
CARPETA_COMPROBANTES_PRIVADOS = os.path.join(
    BASE_DIR, "instance", "comprobantes_privados"
)
ARCHIVO_PRIVADO_PREFIX = "privado-"
ARCHIVO_CERTIFICADO_PREFIX = "privado-cert-"
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
app.config["UPLOAD_FOLDER"] = CARPETA_SUBIDAS
app.config["PRIVATE_UPLOAD_FOLDER"] = CARPETA_COMPROBANTES_PRIVADOS
app.teardown_appcontext(base.close_db)

os.makedirs(CARPETA_SUBIDAS, exist_ok=True)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
ZONA_SANTIAGO = ZoneInfo("America/Santiago")


def fecha_hoy():
    """Fecha civil de Chile, independiente de la zona horaria del servidor."""
    return datetime.now(ZONA_SANTIAGO).date()


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


def es_archivo_privado(nombre):
    return str(nombre or "").startswith(ARCHIVO_PRIVADO_PREFIX)


app.jinja_env.globals["es_archivo_privado"] = es_archivo_privado


def usuario_actual():
    uid = session.get("usuario_id")
    if not uid:
        return None
    return consultar(
        "SELECT * FROM usuarios WHERE id = ? AND activo = 1 "
        "AND estado_aprobacion = 'Aprobada'",
        (uid,), uno=True,
    )


def puede(recurso, usuario=None):
    usuario = usuario or usuario_actual()
    if not usuario:
        return False
    if usuario["rol"] == "administrador":
        return True
    if usuario["rol"] == "coordinador":
        if recurso == "crear_vecinos":
            return obtener_config()["coordinador_crear_vecinos"] == "1"
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


def carpeta_subidas():
    return app.config.get("UPLOAD_FOLDER", CARPETA_SUBIDAS)


def carpeta_privada():
    return app.config.get("PRIVATE_UPLOAD_FOLDER", CARPETA_COMPROBANTES_PRIVADOS)


def guardar_archivo(campo, privado=False, prefijo_privado=None, extensiones=None):
    archivo = request.files.get(campo)
    if not archivo or not archivo.filename:
        return None
    nombre = secure_filename(archivo.filename)
    extension = nombre.rsplit(".", 1)[-1].lower() if "." in nombre else ""
    extensiones_permitidas = extensiones or EXTENSIONES_PERMITIDAS
    if extension not in extensiones_permitidas:
        flash(f"El archivo «{archivo.filename}» no es de un tipo permitido.", "error")
        return None
    marca = datetime.now().strftime("%Y%m%d%H%M%S")
    nombre_final = f"{marca}_{secrets.token_hex(6)}_{nombre}"
    carpeta = carpeta_privada() if privado else carpeta_subidas()
    os.makedirs(carpeta, exist_ok=True)
    archivo.save(os.path.join(carpeta, nombre_final))
    if privado:
        return f"{prefijo_privado or ARCHIVO_PRIVADO_PREFIX}{nombre_final}"
    return nombre_final


def migrar_comprobantes_privados():
    """Mueve a almacenamiento no público los respaldos financieros ya cargados."""
    os.makedirs(carpeta_privada(), exist_ok=True)
    with app.app_context():
        filas = consultar(
            "SELECT archivo FROM rendiciones WHERE archivo IS NOT NULL AND archivo != '' "
            "UNION SELECT archivo FROM documentos "
            "WHERE categoria = 'Rendiciones de cuentas' AND archivo IS NOT NULL AND archivo != ''"
        )
        for fila in filas:
            nombre = fila["archivo"]
            if es_archivo_privado(nombre):
                continue
            if not nombre or os.path.basename(nombre) != nombre or nombre in (".", ".."):
                continue

            origen = os.path.join(carpeta_subidas(), nombre)
            destino = os.path.join(carpeta_privada(), nombre)
            if os.path.isfile(origen) and not os.path.islink(origen):
                if not os.path.exists(destino):
                    shutil.copy2(origen, destino)

            privado = f"{ARCHIVO_PRIVADO_PREFIX}{nombre}"
            ejecutar("UPDATE rendiciones SET archivo = ? WHERE archivo = ?", (privado, nombre))
            ejecutar("UPDATE documentos SET archivo = ? WHERE archivo = ?", (privado, nombre))

            quedan_referencias = consultar(
                "SELECT 1 FROM rendiciones WHERE archivo = ? "
                "UNION ALL SELECT 1 FROM documentos WHERE archivo = ? LIMIT 1",
                (nombre, nombre), uno=True,
            )
            if not quedan_referencias and os.path.lexists(origen):
                os.remove(origen)

        ya_privados = consultar(
            "SELECT archivo FROM rendiciones WHERE archivo LIKE 'privado-%' "
            "UNION SELECT archivo FROM documentos WHERE archivo LIKE 'privado-%'"
        )
        for fila in ya_privados:
            nombre = fila["archivo"][len(ARCHIVO_PRIVADO_PREFIX):]
            if not nombre or os.path.basename(nombre) != nombre:
                continue
            origen = os.path.join(carpeta_subidas(), nombre)
            destino = os.path.join(carpeta_privada(), nombre)
            if os.path.lexists(origen):
                if os.path.isfile(origen) and not os.path.islink(origen):
                    if not os.path.exists(destino):
                        shutil.copy2(origen, destino)
                quedan_referencias = consultar(
                    "SELECT 1 FROM rendiciones WHERE archivo = ? "
                    "UNION ALL SELECT 1 FROM documentos WHERE archivo = ? LIMIT 1",
                    (nombre, nombre), uno=True,
                )
                if not quedan_referencias:
                    os.remove(origen)


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
            campo("imagen_url", "Imagen para la noticia (opcional)", "imagen",
                  ayuda="Sube una imagen JPG, PNG o WebP, o pega un enlace directo."),
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
            campo("archivo", "Comprobante o respaldo privado", "archivo",
                  ayuda="Solo lo puede consultar la administración."),
        ],
        "extra_nuevo": {"fecha_publicacion": lambda: datetime.now().isoformat(timespec="seconds")},
        "movimientos": "rendicion_id",
        "archivo_privado": True,
    },
    "usuarios": {
        "titulo": "Usuarios del sistema",
        "singular": "usuario",
        "tabla": "usuarios",
        "orden": "rol, nombre",
        "columnas": [("nombre", "Nombre"), ("usuario", "Usuario"), ("rol", "Perfil"),
                     ("estado_aprobacion", "Aprobación"), ("activo", "Activo")],
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
    "panel", "reservas", "certificados", "servicios", "servicios_pendientes",
    "avance_proyectos", "noticias", "documentos",
}

MENU_PANEL = [
    ("Solicitudes vecinales", [
        ("reservas", "Reservas de la sede", "🗓️"),
        ("certificados", "Certificados de residencia", "📄"),
        ("servicios_pendientes", "Aprobar directorio", "🧰"),
        ("mensajes", "Mensajes de vecinos", "✉️"),
        ("servicios", "Directorio de servicios", "🧰"),
    ]),
    ("Información institucional", [
        ("noticias", "Noticias y actividades", "📢"),
        ("documentos", "Documentos", "📚"),
        ("directiva", "Directiva", "👥"),
        ("contenido", "Textos del sitio", "⚙️"),
    ]),
    ("Proyectos", [
        ("proyectos", "Ficha de proyectos y presupuestos", "🏗️"),
        ("avance_proyectos", "Avances de proyectos", "📈"),
    ]),
    ("Transparencia", [
        ("rendiciones", "Rendiciones de cuentas", "💰"),
        ("eventos", "Balances de actividades", "🎉"),
    ]),
    ("Configuración", [
        ("bloques", "Actividades fijas de la sede", "🏛️"),
        ("crear_vecinos", "Crear cuenta vecinal", "➕"),
        ("permisos", "Permisos del coordinador", "🛡️"),
        ("usuarios_pendientes", "Aprobar cuentas vecinales", "🛡️"),
        ("usuarios", "Usuarios del sistema", "🔐"),
    ]),
]


ENDPOINTS_ESPECIALES = {
    "reservas": "panel_reservas",
    "certificados": "panel_certificados",
    "mensajes": "panel_mensajes",
    "contenido": "panel_contenido",
    "avance_proyectos": "panel_avances_proyectos",
    "usuarios_pendientes": "panel_usuarios_pendientes",
    "crear_vecinos": "panel_crear_vecino",
    "permisos": "panel_permisos_coordinador",
    "servicios_pendientes": "panel_servicios_pendientes",
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
            + consultar("SELECT COUNT(*) c FROM servicios WHERE aprobado = 0", uno=True)["c"]
        )
        if puede("mensajes", usuario):
            pendientes += consultar(
                "SELECT COUNT(*) c FROM mensajes WHERE leido = 0", uno=True
            )["c"]
        if puede("usuarios", usuario):
            pendientes += consultar(
                "SELECT COUNT(*) c FROM usuarios WHERE rol = 'vecino' "
                "AND estado_aprobacion = 'Pendiente'", uno=True
            )["c"]
    menu_visible = [
        (categoria, [enlace for enlace in enlaces if puede(enlace[0], usuario)])
        for categoria, enlaces in MENU_PANEL
    ]
    menu_visible = [(categoria, enlaces) for categoria, enlaces in menu_visible if enlaces]
    return {
        "conf": conf,
        "usuario": usuario,
        "puede": puede,
        "url_panel": url_panel,
        "anio_actual": fecha_hoy().year,
        "menu_panel": menu_visible,
        "pendientes_panel": pendientes,
    }


# ---------------------------------------------------------------------------
# Sitio público
# ---------------------------------------------------------------------------
def proyectos_con_ultimo_avance(condiciones, parametros=(), orden="p.nombre"):
    """Devuelve proyectos con su actualización de avance más reciente, si existe."""
    where = " AND ".join(condiciones)
    return consultar(
        "SELECT p.*, a.porcentaje AS avance_porcentaje, a.detalle AS avance_detalle, "
        "a.fecha_actualizacion AS avance_fecha "
        "FROM proyectos p LEFT JOIN avances_proyecto a ON a.id = ("
        "SELECT id FROM avances_proyecto WHERE proyecto_id = p.id ORDER BY id DESC LIMIT 1) "
        f"WHERE {where} ORDER BY {orden}",
        parametros,
    )


def historial_de_avances(proyecto_ids):
    if not proyecto_ids:
        return {}
    marcadores = ", ".join("?" for _ in proyecto_ids)
    filas = consultar(
        "SELECT proyecto_id, porcentaje, detalle, fecha_actualizacion "
        f"FROM avances_proyecto WHERE proyecto_id IN ({marcadores}) ORDER BY id DESC",
        tuple(proyecto_ids),
    )
    historial = {}
    for fila in filas:
        historial.setdefault(fila["proyecto_id"], []).append(fila)
    return historial


@app.route("/")
def index():
    noticias = consultar(
        "SELECT * FROM noticias WHERE publicado = 1 ORDER BY fecha_publicacion DESC LIMIT 3"
    )
    proyectos = proyectos_con_ultimo_avance(
        ["p.horizonte = 'Actual'"],
        orden="COALESCE(a.id, 0) DESC, p.fecha_actualizacion DESC LIMIT 3",
    )
    semana = semana_de(fecha_hoy())
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
    condiciones = ["p.horizonte = 'Actual'"]
    params = []
    if estado in ESTADOS_PROYECTO:
        condiciones.append("p.estado = ?")
        params.append(estado)
    lista = proyectos_con_ultimo_avance(
        condiciones, params, "COALESCE(a.id, 0) DESC, p.fecha_actualizacion DESC"
    )
    return render_template(
        "proyectos.html",
        proyectos=lista,
        estados=ESTADOS_PROYECTO,
        filtro_estado=estado,
        historial_por_proyecto=historial_de_avances([p["id"] for p in lista]),
    )


@app.route("/plan-maestro")
def plan_maestro():
    actuales = proyectos_con_ultimo_avance(
        ["p.horizonte = 'Actual'"], orden="p.eje, p.nombre"
    )
    futuros = proyectos_con_ultimo_avance(
        ["p.horizonte = 'Futuro'"], orden="p.eje, p.nombre"
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
    usuario = usuario_actual()
    rendiciones = []
    eventos = []
    documentos_financieros = []
    if usuario:
        for r in consultar("SELECT * FROM rendiciones ORDER BY fecha_publicacion DESC"):
            movs = consultar(
                "SELECT * FROM movimientos WHERE rendicion_id = ? ORDER BY fecha", (r["id"],)
            )
            rendiciones.append({"datos": r, "movimientos": movs, "totales": totales_de(movs)})

        for e in consultar("SELECT * FROM eventos WHERE publicado = 1 ORDER BY fecha DESC"):
            movs = consultar(
                "SELECT * FROM movimientos WHERE evento_id = ? ORDER BY fecha", (e["id"],)
            )
            eventos.append({"datos": e, "movimientos": movs, "totales": totales_de(movs)})
        documentos_financieros = consultar(
            "SELECT * FROM documentos WHERE categoria = 'Rendiciones de cuentas' "
            "ORDER BY fecha_publicacion DESC"
        )

    return render_template(
        "transparencia.html", rendiciones=rendiciones, eventos=eventos,
        documentos_financieros=documentos_financieros, usuario=usuario,
    )


@app.route("/documentos")
def documentos():
    categoria = request.args.get("categoria") or ""
    categorias_publicas = [
        c for c in CATEGORIAS_DOCUMENTO if c != "Rendiciones de cuentas"
    ]
    if categoria == "Rendiciones de cuentas":
        abort(404)
    if categoria not in categorias_publicas:
        categoria = ""
    sql = (
        "SELECT * FROM documentos WHERE categoria != 'Rendiciones de cuentas' "
        "AND (archivo IS NULL OR archivo = '' OR archivo NOT LIKE 'privado-%')"
    )
    params = []
    if categoria:
        sql += " AND categoria = ?"
        params.append(categoria)
    sql += " ORDER BY categoria, fecha_publicacion DESC"
    return render_template(
        "documentos.html",
        documentos=consultar(sql, params),
        categorias=categorias_publicas,
        filtro_categoria=categoria,
    )


@app.route("/archivos/<path:nombre>")
def archivo(nombre):
    if nombre.startswith(ARCHIVO_CERTIFICADO_PREFIX):
        if not puede("certificados"):
            abort(404)
        nombre_privado = nombre[len(ARCHIVO_CERTIFICADO_PREFIX):]
    elif es_archivo_privado(nombre):
        if not usuario_actual():
            abort(404)
        nombre_privado = nombre[len(ARCHIVO_PRIVADO_PREFIX):]
    else:
        return send_from_directory(carpeta_subidas(), nombre)

    respuesta = send_from_directory(
        carpeta_privada(), nombre_privado, as_attachment=True
    )
    respuesta.headers["Cache-Control"] = "private, no-store"
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["Referrer-Policy"] = "no-referrer"
    return respuesta


# ---------------------------------------------------------------------------
# Sede vecinal: calendario y reservas
# ---------------------------------------------------------------------------
def minutos_de_hora(valor):
    try:
        horas, minutos = str(valor).split(":", 1)
        horas, minutos = int(horas), int(minutos[:2])
        if 0 <= horas <= 23 and 0 <= minutos <= 59:
            return horas * 60 + minutos
    except (TypeError, ValueError):
        pass
    return None


def hora_desde_minutos(valor):
    return f"{valor // 60:02d}:{valor % 60:02d}"


def intervalos_disponibles(dia, actividades, apertura, cierre):
    """Resta actividades y reservas del horario de apertura configurado."""
    inicio = minutos_de_hora(apertura)
    fin = minutos_de_hora(cierre)
    hoy = fecha_hoy()
    if inicio is None or fin is None or fin <= inicio or dia < hoy:
        return []
    if dia == hoy:
        ahora = datetime.now(ZONA_SANTIAGO)
        inicio = max(inicio, ahora.hour * 60 + ahora.minute + bool(ahora.second))

    ocupados = []
    for actividad in actividades:
        desde = minutos_de_hora(actividad["hora_inicio"])
        hasta = minutos_de_hora(actividad["hora_fin"])
        if desde is None or hasta is None or hasta <= desde:
            continue
        desde, hasta = max(inicio, desde), min(fin, hasta)
        if hasta > desde:
            ocupados.append((desde, hasta))
    ocupados.sort()

    libres = []
    cursor = inicio
    for desde, hasta in ocupados:
        if desde > cursor:
            libres.append((hora_desde_minutos(cursor), hora_desde_minutos(desde)))
        cursor = max(cursor, hasta)
    if cursor < fin:
        libres.append((hora_desde_minutos(cursor), hora_desde_minutos(fin)))
    return libres


def semana_de(dia_referencia, conf=None):
    """Devuelve la semana de lunes a domingo, con actividades y horas libres."""
    conf = conf or obtener_config()
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
                "horarios": intervalos_disponibles(
                    dia, actividades,
                    conf.get("horario_sede_inicio", "09:00"),
                    conf.get("horario_sede_fin", "21:00"),
                ),
                "pasado": dia < fecha_hoy(),
                "hoy": dia == fecha_hoy(),
            }
        )
    return {"lunes": lunes, "domingo": domingo, "dias": dias}


def fecha_del_mes(valor):
    try:
        anio, mes = (int(parte) for parte in str(valor).split("-", 1))
        return date(anio, mes, 1)
    except (TypeError, ValueError):
        hoy = fecha_hoy()
        return date(hoy.year, hoy.month, 1)


@app.route("/sede")
def sede():
    conf = obtener_config()
    vista_mes = request.args.get("vista") == "mes" or bool(request.args.get("mes"))
    solo_disponibles = request.args.get("disponibles") == "1"
    referencia = a_fecha(request.args.get("semana")) or fecha_hoy()
    semana = semana_de(referencia, conf)
    mes_ref = fecha_del_mes(
        request.args.get("mes") or referencia.strftime("%Y-%m")
    )
    semanas_mes = []
    if vista_mes:
        semanas_mes = [
            semana_de(fechas[0], conf)["dias"]
            for fechas in calendar.Calendar(firstweekday=0).monthdatescalendar(
                mes_ref.year, mes_ref.month
            )
        ]
    dias_visibles = semana["dias"]
    if solo_disponibles:
        dias_visibles = [dia for dia in dias_visibles if dia["horarios"]]
    return render_template(
        "sede.html",
        semana=semana,
        dias_visibles=dias_visibles,
        vista_mes=vista_mes,
        semanas_mes=semanas_mes,
        mes_ref=mes_ref,
        mes_nombre=MESES[mes_ref.month - 1].capitalize(),
        mes_anterior=(mes_ref.replace(day=1) - timedelta(days=1)).strftime("%Y-%m"),
        mes_siguiente=(mes_ref.replace(day=28) + timedelta(days=4)).replace(day=1).strftime("%Y-%m"),
        solo_disponibles=solo_disponibles,
        anterior=(semana["lunes"] - timedelta(days=7)).isoformat(),
        siguiente=(semana["lunes"] + timedelta(days=7)).isoformat(),
        bloques=consultar(
            "SELECT * FROM bloques_fijos WHERE activo = 1 ORDER BY dia_semana, hora_inicio"
        ),
        dias_semana=DIAS_SEMANA,
        horario_inicio=conf.get("horario_sede_inicio", "09:00"),
        horario_fin=conf.get("horario_sede_fin", "21:00"),
    )


@app.route("/sede/reservar", methods=["GET", "POST"])
def reservar():
    usuario = usuario_actual()
    datos = {
        "fecha": request.args.get("fecha", ""),
        "hora_inicio": request.args.get("hora_inicio", ""),
        "hora_fin": request.args.get("hora_fin", ""),
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
        hora_inicio = minutos_de_hora(datos["hora_inicio"])
        hora_fin = minutos_de_hora(datos["hora_fin"])
        conf = obtener_config()
        apertura = minutos_de_hora(conf.get("horario_sede_inicio", "09:00"))
        cierre = minutos_de_hora(conf.get("horario_sede_fin", "21:00"))
        if faltan:
            flash("Completa los datos obligatorios del formulario.", "error")
        elif not fecha:
            flash("La fecha no es válida.", "error")
        elif fecha < fecha_hoy():
            flash("No se puede reservar una fecha que ya pasó.", "error")
        elif hora_inicio is None or hora_fin is None:
            flash("Elige horas válidas para la reserva.", "error")
        elif hora_fin <= hora_inicio:
            flash("La hora de término debe ser posterior a la de inicio.", "error")
        elif (apertura is not None and hora_inicio < apertura) or (
            cierre is not None and hora_fin > cierre
        ):
            flash(
                f"La sede recibe reservas entre {conf.get('horario_sede_inicio', '09:00')} "
                f"y {conf.get('horario_sede_fin', '21:00')}.", "error"
            )
        elif hay_choque(fecha, datos["hora_inicio"], datos["hora_fin"]):
            flash(
                "Ese horario ya está ocupado en la sede. Revisa el calendario y elige otro.",
                "error",
            )
        else:
            ejecutar(
                "INSERT INTO reservas (fecha, hora_inicio, hora_fin, actividad, solicitante, "
                "telefono, email, personas, estado, observacion, revisada_por, fecha_solicitud, "
                "usuario_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pendiente', ?, '', ?, ?)",
                (
                    fecha.isoformat(), datos["hora_inicio"], datos["hora_fin"],
                    datos["actividad"], datos["solicitante"], datos["telefono"],
                    datos["email"], int(datos["personas"] or 0) or None,
                    datos["observacion"],
                    datetime.now().isoformat(timespec="seconds"),
                    usuario["id"] if usuario else None,
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
    crear_cuenta = False
    datos = {"nombre": usuario["nombre"] if usuario else "",
             "email": (usuario["email"] if usuario else "") or ""}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip() for k in
                 ("nombre", "rut", "direccion", "telefono", "email", "motivo")}
        crear_cuenta = not usuario and request.form.get("crear_cuenta") == "on"
        nuevo_usuario = (request.form.get("nuevo_usuario") or "").strip()
        nueva_clave = request.form.get("nueva_clave") or ""
        confirmar_clave = request.form.get("confirmar_clave") or ""
        documento = request.files.get("documento_domicilio")
        extension_documento = ""
        if documento and documento.filename:
            nombre_documento = secure_filename(documento.filename)
            extension_documento = (
                nombre_documento.rsplit(".", 1)[-1].lower()
                if "." in nombre_documento else ""
            )
        if request.form.get("entiendo_revision") != "on":
            flash("Confirma que entiendes quién revisará los datos y el documento.", "error")
        elif not documento or not documento.filename:
            flash("Adjunta un documento para acreditar tu domicilio.", "error")
        elif extension_documento not in {"pdf", "jpg", "jpeg", "png", "webp"}:
            flash("El documento debe ser PDF, JPG, PNG o WebP.", "error")
        elif not datos["nombre"] or not datos["rut"] or not datos["direccion"]:
            flash("Completa tu nombre, RUT y dirección.", "error")
        elif crear_cuenta and (
            len(nuevo_usuario) < 3 or len(nueva_clave) < 8
        ):
            flash(
                "Para crear la cuenta, ingresa un usuario de al menos 3 caracteres "
                "y una clave de al menos 8 caracteres.",
                "error",
            )
        elif crear_cuenta and nueva_clave != confirmar_clave:
            flash("Las claves no coinciden.", "error")
        elif crear_cuenta and consultar(
            "SELECT id FROM usuarios WHERE usuario = ?", (nuevo_usuario,), uno=True
        ):
            flash("Ese nombre de usuario ya existe. Elige otro.", "error")
        else:
            codigo = secrets.token_hex(12).upper()
            while consultar(
                "SELECT id FROM certificados WHERE codigo_seguimiento = ?", (codigo,), uno=True
            ):
                codigo = secrets.token_hex(12).upper()
            archivo_domicilio = guardar_archivo(
                "documento_domicilio", privado=True,
                prefijo_privado=ARCHIVO_CERTIFICADO_PREFIX,
                extensiones={"pdf", "jpg", "jpeg", "png", "webp"},
            )
            if not archivo_domicilio:
                return render_template(
                    "certificado.html", datos=datos, crear_cuenta=crear_cuenta,
                    requisitos=[r.strip() for r in obtener_config()["requisitos_certificado"].split("\n") if r.strip()],
                )
            db = base.get_db()
            try:
                usuario_id = usuario["id"] if usuario else None
                if crear_cuenta:
                    cursor = db.execute(
                        "INSERT INTO usuarios (nombre, usuario, email, clave_hash, rol, activo, "
                        "estado_aprobacion, fecha_creacion) "
                        "VALUES (?, ?, ?, ?, 'vecino', 0, 'Pendiente', ?)",
                        (
                            datos["nombre"], nuevo_usuario, datos["email"],
                            generate_password_hash(nueva_clave),
                            datetime.now().isoformat(timespec="seconds"),
                        ),
                    )
                    usuario_id = cursor.lastrowid
                db.execute(
                    "INSERT INTO certificados (nombre, rut, direccion, telefono, email, motivo, "
                    "estado, observacion, fecha_solicitud, codigo_seguimiento, usuario_id, "
                    "archivo_domicilio) VALUES (?, ?, ?, ?, ?, ?, 'Recibida', '', ?, ?, ?, ?)",
                    (
                        datos["nombre"], datos["rut"], datos["direccion"], datos["telefono"],
                        datos["email"], datos["motivo"],
                        datetime.now().isoformat(timespec="seconds"), codigo, usuario_id,
                        archivo_domicilio,
                    ),
                )
                db.commit()
            except sqlite3.IntegrityError:
                db.rollback()
                nombre_guardado = archivo_domicilio[len(ARCHIVO_CERTIFICADO_PREFIX):]
                try:
                    os.remove(os.path.join(carpeta_privada(), nombre_guardado))
                except FileNotFoundError:
                    pass
                flash(
                    "No pudimos guardar la solicitud. Revisa si el usuario elegido ya existe "
                    "e inténtalo nuevamente.",
                    "error",
                )
                return redirect(url_for("certificado"))
            if crear_cuenta:
                flash(
                    "Tu solicitud de certificado fue recibida. La cuenta quedó pendiente "
                    "de aprobación por la directiva; podrás ingresar cuando la aprueben.",
                    "info",
                )
            session["codigo_seguimiento_reciente"] = codigo
            return redirect(url_for("estado_certificado"))
    requisitos = [
        r.strip() for r in obtener_config()["requisitos_certificado"].split("\n") if r.strip()
    ]
    return render_template(
        "certificado.html", datos=datos, crear_cuenta=crear_cuenta, requisitos=requisitos
    )


@app.route("/certificado-residencia/estado", methods=["GET", "POST"])
def estado_certificado():
    codigo_nuevo = session.pop("codigo_seguimiento_reciente", "")
    codigo = (
        (request.form.get("codigo") or "").strip().upper()
        if request.method == "POST"
        else codigo_nuevo
    )
    solicitud = None
    if codigo:
        solicitud = consultar(
            "SELECT codigo_seguimiento, estado, fecha_solicitud FROM certificados "
            "WHERE codigo_seguimiento = ?",
            (codigo,), uno=True,
        )
        if not solicitud:
            flash("No encontramos una solicitud con ese código.", "error")
    respuesta = app.make_response(render_template(
        "estado_certificado.html", solicitud=solicitud, codigo=codigo,
        codigo_nuevo=codigo_nuevo,
    ))
    respuesta.headers["Cache-Control"] = "no-store"
    respuesta.headers["Referrer-Policy"] = "no-referrer"
    respuesta.headers["X-Robots-Tag"] = "noindex, nofollow"
    return respuesta


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
            "SELECT * FROM usuarios WHERE usuario = ?", (nombre_usuario,), uno=True
        )
        if fila and check_password_hash(fila["clave_hash"], clave):
            if fila["estado_aprobacion"] == "Pendiente":
                flash("Tu cuenta está pendiente de aprobación por la directiva.", "info")
            elif not fila["activo"]:
                flash("La cuenta está desactivada. Contacta a la directiva.", "error")
            else:
                session["usuario_id"] = fila["id"]
                destino = request.args.get("next")
                if not destino or not destino.startswith("/"):
                    destino = url_for("panel") if fila["rol"] != "vecino" else url_for("mis_solicitudes")
                return redirect(destino)
        else:
            flash("Usuario o clave incorrectos.", "error")
    return render_template("login.html")


@app.route("/registro", methods=["GET", "POST"])
def registro():
    datos = {}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip()
                 for k in ("nombre", "usuario", "email")}
        clave = request.form.get("clave") or ""
        confirmar_clave = request.form.get("confirmar_clave") or ""
        if not datos["nombre"] or len(datos["usuario"]) < 3 or len(clave) < 8:
            flash(
                "Completa tu nombre, un usuario de al menos 3 caracteres "
                "y una clave de al menos 8 caracteres.",
                "error",
            )
        elif clave != confirmar_clave:
            flash("Las claves no coinciden.", "error")
        elif consultar("SELECT id FROM usuarios WHERE usuario = ?", (datos["usuario"],), uno=True):
            flash("Ese nombre de usuario ya existe, elige otro.", "error")
        else:
            ejecutar(
                "INSERT INTO usuarios (nombre, usuario, email, clave_hash, rol, activo, "
                "estado_aprobacion, fecha_creacion) "
                "VALUES (?, ?, ?, ?, 'vecino', 0, 'Pendiente', ?)",
                (datos["nombre"], datos["usuario"], datos["email"],
                 generate_password_hash(clave),
                 datetime.now().isoformat(timespec="seconds")),
            )
            flash(
                "Recibimos tu solicitud de cuenta. La directiva debe aprobarla antes "
                "de que puedas ingresar.",
                "info",
            )
            return redirect(url_for("login"))
    return render_template("registro.html", datos=datos)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/mis-solicitudes")
@login_requerido
def mis_solicitudes():
    usuario = usuario_actual()
    reservas = consultar(
        "SELECT * FROM reservas WHERE usuario_id = ? ORDER BY fecha DESC, hora_inicio",
        (usuario["id"],),
    )
    certificados = consultar(
        "SELECT estado, fecha_solicitud FROM certificados "
        "WHERE usuario_id = ? ORDER BY fecha_solicitud DESC",
        (usuario["id"],),
    )
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
        "mensajes_nuevos": (
            consultar("SELECT COUNT(*) c FROM mensajes WHERE leido = 0", uno=True)["c"]
            if puede("mensajes", usuario) else 0
        ),
        "servicios_por_aprobar": consultar(
            "SELECT COUNT(*) c FROM servicios WHERE aprobado = 0", uno=True)["c"],
        "proyectos_en_ejecucion": consultar(
            "SELECT COUNT(*) c FROM proyectos WHERE horizonte = 'Actual' "
            "AND estado = 'En ejecución'", uno=True)["c"],
        "noticias": consultar("SELECT COUNT(*) c FROM noticias", uno=True)["c"],
        "proyectos": consultar("SELECT COUNT(*) c FROM proyectos", uno=True)["c"],
        "usuarios_pendientes": (
            consultar(
                "SELECT COUNT(*) c FROM usuarios WHERE rol = 'vecino' "
                "AND estado_aprobacion = 'Pendiente'",
                uno=True,
            )["c"] if puede("usuarios", usuario) else 0
        ),
    }
    proximas = consultar(
        "SELECT * FROM reservas WHERE fecha >= ? AND estado = 'Aprobada' "
        "ORDER BY fecha LIMIT 5", (fecha_hoy().isoformat(),)
    )
    return render_template("panel/inicio.html", resumen=resumen, proximas=proximas)


@app.route("/panel/usuarios-pendientes")
@permiso_requerido("usuarios")
def panel_usuarios_pendientes():
    usuarios_pendientes = consultar(
        "SELECT id, nombre, usuario, email, fecha_creacion FROM usuarios "
        "WHERE rol = 'vecino' AND estado_aprobacion = 'Pendiente' ORDER BY fecha_creacion"
    )
    return render_template(
        "panel/usuarios_pendientes.html", usuarios_pendientes=usuarios_pendientes
    )


@app.route("/panel/usuarios/<int:usuario_id>/aprobar", methods=["POST"])
@permiso_requerido("usuarios")
def panel_usuario_aprobar(usuario_id):
    usuario = consultar(
        "SELECT id FROM usuarios WHERE id = ? AND rol = 'vecino' "
        "AND estado_aprobacion = 'Pendiente'",
        (usuario_id,), uno=True,
    )
    if not usuario:
        abort(404)
    ejecutar(
        "UPDATE usuarios SET estado_aprobacion = 'Aprobada', activo = 1 WHERE id = ?",
        (usuario_id,),
    )
    flash("La cuenta vecinal fue aprobada y ya puede ingresar.", "success")
    return redirect(url_for("panel_usuarios_pendientes"))


@app.route("/panel/crear-vecino", methods=["GET", "POST"])
@permiso_requerido("crear_vecinos")
def panel_crear_vecino():
    datos = {}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip()
                 for k in ("nombre", "usuario", "email")}
        clave = request.form.get("clave") or ""
        confirmar_clave = request.form.get("confirmar_clave") or ""
        if not datos["nombre"] or len(datos["usuario"]) < 3 or len(clave) < 8:
            flash("Ingresa el nombre, un usuario de 3 caracteres y una clave de 8 caracteres.", "error")
        elif clave != confirmar_clave:
            flash("Las claves no coinciden.", "error")
        elif consultar("SELECT id FROM usuarios WHERE usuario = ?", (datos["usuario"],), uno=True):
            flash("Ese nombre de usuario ya existe.", "error")
        else:
            actor = usuario_actual()
            aprobado = actor["rol"] == "administrador"
            ejecutar(
                "INSERT INTO usuarios (nombre, usuario, email, clave_hash, rol, activo, "
                "estado_aprobacion, fecha_creacion) VALUES (?, ?, ?, ?, 'vecino', ?, ?, ?)",
                (
                    datos["nombre"], datos["usuario"], datos["email"],
                    generate_password_hash(clave), int(aprobado),
                    "Aprobada" if aprobado else "Pendiente",
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            if aprobado:
                flash("La cuenta vecinal fue creada y quedó activa.", "success")
            else:
                flash("La cuenta quedó pendiente de aprobación administrativa.", "info")
            return redirect(url_for("panel_crear_vecino"))
    return render_template("panel/crear_vecino.html", datos=datos)


@app.route("/panel/permisos-coordinador", methods=["GET", "POST"])
@permiso_requerido("usuarios")
def panel_permisos_coordinador():
    if request.method == "POST":
        permitir = "1" if request.form.get("crear_vecinos") == "on" else "0"
        base.guardar_config("coordinador_crear_vecinos", permitir)
        flash(
            "Permiso actualizado. El coordinador "
            + ("puede crear cuentas vecinales." if permitir == "1" else "ya no puede crear cuentas vecinales."),
            "success",
        )
        return redirect(url_for("panel_permisos_coordinador"))
    return render_template(
        "panel/permisos_coordinador.html",
        puede_crear_vecinos=obtener_config()["coordinador_crear_vecinos"] == "1",
    )


@app.route("/panel/directorio-pendiente")
@permiso_requerido("servicios_pendientes")
def panel_servicios_pendientes():
    servicios = consultar(
        "SELECT * FROM servicios WHERE aprobado = 0 ORDER BY fecha_solicitud DESC"
    )
    return render_template("panel/servicios_pendientes.html", servicios=servicios)


@app.route("/panel/directorio/<int:servicio_id>/aprobar", methods=["POST"])
@permiso_requerido("servicios_pendientes")
def panel_servicio_aprobar(servicio_id):
    servicio = consultar(
        "SELECT id FROM servicios WHERE id = ? AND aprobado = 0", (servicio_id,), uno=True
    )
    if not servicio:
        abort(404)
    ejecutar("UPDATE servicios SET aprobado = 1 WHERE id = ?", (servicio_id,))
    flash("El servicio fue aprobado y ya aparece en el directorio.", "success")
    return redirect(url_for("panel_servicios_pendientes"))


# --- Avances de proyectos -------------------------------------------------
@app.route("/panel/avances-proyectos")
@permiso_requerido("avance_proyectos")
def panel_avances_proyectos():
    lista = proyectos_con_ultimo_avance(
        ["p.horizonte = 'Actual'", "p.estado = 'En ejecución'"], orden="p.nombre"
    )
    return render_template(
        "panel/avances_proyectos.html",
        proyectos=lista,
        historial_por_proyecto=historial_de_avances([p["id"] for p in lista]),
    )


@app.route("/panel/avances-proyectos/<int:proyecto_id>", methods=["POST"])
@permiso_requerido("avance_proyectos")
def panel_proyecto_avance(proyecto_id):
    proyecto = consultar(
        "SELECT id FROM proyectos WHERE id = ? AND horizonte = 'Actual' "
        "AND estado = 'En ejecución'",
        (proyecto_id,), uno=True,
    )
    if not proyecto:
        abort(404)

    porcentaje = (request.form.get("porcentaje") or "").strip()
    detalle = (request.form.get("detalle") or "").strip()
    if not porcentaje.isdigit() or not 0 <= int(porcentaje) <= 100:
        flash("Ingresa un porcentaje entre 0 y 100.", "error")
    elif not detalle or len(detalle) > 500:
        flash("Describe el avance en hasta 500 caracteres.", "error")
    else:
        usuario = usuario_actual()
        ejecutar(
            "INSERT INTO avances_proyecto "
            "(proyecto_id, porcentaje, detalle, fecha_actualizacion, actualizado_por) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                proyecto_id, int(porcentaje), detalle,
                datetime.now().isoformat(timespec="seconds"), usuario["nombre"],
            ),
        )
        flash("El avance quedó registrado y se mostrará en el sitio público.", "success")
    return redirect(url_for("panel_avances_proyectos"))


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
    solicitudes = consultar(sql, params)
    return render_template(
        "panel/certificados.html", solicitudes=solicitudes,
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
    ("horario_sede_inicio", "Apertura para reservas de sede", "hora"),
    ("horario_sede_fin", "Cierre para reservas de sede", "hora"),
    ("email_contacto", "Correo de contacto", "texto"),
    ("telefono_contacto", "Teléfono de contacto", "texto"),
    ("whatsapp_grupo", "Enlace al grupo de WhatsApp", "texto"),
    ("facebook", "Enlace a Facebook", "texto"),
    ("instagram", "Enlace a Instagram", "texto"),
    ("plan_maestro_intro", "Introducción del plan maestro", "textarea"),
    ("requisitos_certificado", "Requisitos del certificado (uno por línea)", "textarea"),
    ("aviso_privacidad", "Aviso de privacidad aprobado por la contraparte", "textarea"),
    ("aviso_uso", "Condiciones de uso aprobadas por la contraparte", "textarea"),
]


@app.route("/panel/contenido", methods=["GET", "POST"])
@permiso_requerido("contenido")
def panel_contenido():
    if request.method == "POST":
        valores = {
            clave: (request.form.get(clave) or "").strip()
            for clave, _, _ in CAMPOS_CONTENIDO
        }
        inicio = minutos_de_hora(valores["horario_sede_inicio"])
        fin = minutos_de_hora(valores["horario_sede_fin"])
        if inicio is None or fin is None:
            flash("Ingresa horas válidas para el horario de la sede.", "error")
            return redirect(url_for("panel_contenido"))
        if fin <= inicio:
            flash("El cierre de la sede debe ser posterior a su apertura.", "error")
            return redirect(url_for("panel_contenido"))
        for clave, valor in valores.items():
            base.guardar_config(clave, valor)
        flash("Los textos del sitio fueron actualizados.", "success")
        return redirect(url_for("panel_contenido"))
    return render_template("panel/contenido.html", campos=CAMPOS_CONTENIDO)


@app.route("/privacidad")
def privacidad():
    conf = obtener_config()
    if not conf["aviso_privacidad"] and not conf["aviso_uso"]:
        abort(404)
    return render_template(
        "privacidad.html", aviso_privacidad=conf["aviso_privacidad"],
        aviso_uso=conf["aviso_uso"],
    )


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
            privado = recurso.get("archivo_privado", False)
            if recurso.get("tabla") == "documentos":
                privado = privado or request.form.get("categoria") == "Rendiciones de cuentas"
            if fila_actual is not None:
                privado = privado or es_archivo_privado(fila_actual[nombre])
            guardado = guardar_archivo(nombre, privado=privado)
            if guardado:
                valores[nombre] = guardado
            elif fila_actual is not None:
                valores[nombre] = fila_actual[nombre]
            else:
                valores[nombre] = ""
        elif c["tipo"] == "imagen":
            subida = guardar_archivo(
                "imagen_archivo", extensiones={"jpg", "jpeg", "png", "webp"}
            )
            valores[nombre] = (
                url_for("static", filename=f"uploads/{subida}")
                if subida else (request.form.get(nombre) or "").strip()
            )
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


def coordinador_actual():
    usuario = usuario_actual()
    return bool(usuario and usuario["rol"] == "coordinador")


def documento_financiero(recurso, fila=None, categoria=None):
    if recurso != "documentos":
        return False
    if categoria is not None:
        return categoria == "Rendiciones de cuentas"
    return bool(fila and fila["categoria"] == "Rendiciones de cuentas")


def definicion_para_panel(recurso, definicion):
    if recurso not in {"documentos", "servicios"} or not coordinador_actual():
        return definicion
    adaptada = dict(definicion)
    adaptada["campos"] = []
    for campo_actual in definicion["campos"]:
        if recurso == "servicios" and campo_actual["nombre"] == "aprobado":
            continue
        c = dict(campo_actual)
        if recurso == "documentos" and c["nombre"] == "categoria":
            c["opciones"] = [
                opcion for opcion in c["opciones"]
                if opcion != "Rendiciones de cuentas"
            ]
        adaptada["campos"].append(c)
    return adaptada


@app.route("/panel/<recurso>")
@login_requerido
def panel_listado(recurso):
    definicion = recurso_o_404(recurso)
    if not puede(recurso):
        flash("Tu perfil no tiene permiso para esta sección.", "error")
        return redirect(url_for("panel"))
    sql = f"SELECT * FROM {definicion['tabla']}"
    if recurso == "documentos" and coordinador_actual():
        sql += " WHERE categoria != 'Rendiciones de cuentas'"
    filas = consultar(f"{sql} ORDER BY {definicion['orden']}")
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
    if request.method == "POST" and documento_financiero(
        recurso, categoria=request.form.get("categoria")
    ) and coordinador_actual():
        abort(403)
    definicion_formulario = definicion_para_panel(recurso, definicion)
    if request.method == "POST":
        valores = valores_desde_formulario(definicion)
        for clave, generador in definicion.get("extra_nuevo", {}).items():
            valores[clave] = generador()
        for clave, generador in definicion.get("extra_siempre", {}).items():
            valores[clave] = generador()
        if recurso == "servicios" and coordinador_actual():
            valores["aprobado"] = 0
        if recurso == "usuarios":
            if "clave_hash" not in valores:
                flash("Debes escribir una clave para el nuevo usuario.", "error")
                return render_template(
                    "panel/formulario.html", recurso=recurso,
                    definicion=definicion_formulario, fila=None)
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
        "panel/formulario.html", recurso=recurso,
        definicion=definicion_formulario, fila=None
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
    if coordinador_actual() and documento_financiero(recurso, fila=fila):
        abort(404)
    definicion_formulario = definicion_para_panel(recurso, definicion)
    if request.method == "POST":
        if documento_financiero(
            recurso, categoria=request.form.get("categoria")
        ) and coordinador_actual():
            abort(403)
        valores = valores_desde_formulario(definicion, fila)
        valores.pop("clave", None)
        for clave, generador in definicion.get("extra_siempre", {}).items():
            valores[clave] = generador()
        if recurso == "servicios" and coordinador_actual():
            valores["aprobado"] = 0
        asignaciones = ", ".join(f"{k} = ?" for k in valores)
        ejecutar(
            f"UPDATE {definicion['tabla']} SET {asignaciones} WHERE id = ?",
            tuple(valores.values()) + (fila_id,),
        )
        flash("Los cambios fueron guardados.", "success")
        return redirect(url_for("panel_listado", recurso=recurso))
    return render_template(
        "panel/formulario.html", recurso=recurso,
        definicion=definicion_formulario, fila=fila
    )


@app.route("/panel/<recurso>/<int:fila_id>/eliminar", methods=["POST"])
@login_requerido
def panel_eliminar(recurso, fila_id):
    definicion = recurso_o_404(recurso)
    if not puede(recurso):
        flash("Tu perfil no tiene permiso para esta sección.", "error")
        return redirect(url_for("panel"))
    usuario = usuario_actual()
    fila = consultar(
        f"SELECT * FROM {definicion['tabla']} WHERE id = ?", (fila_id,), uno=True
    )
    if not fila:
        abort(404)
    if coordinador_actual() and documento_financiero(recurso, fila=fila):
        abort(404)
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
        fecha = a_fecha(request.form.get("fecha")) or fecha_hoy()
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
migrar_comprobantes_privados()


if __name__ == "__main__":
    # En macOS el puerto 5000 lo ocupa el "Receptor AirPlay", por eso se usa 5001
    # por defecto. Se puede cambiar con la variable de entorno PORT.
    puerto = int(os.environ.get("PORT", "5001"))
    app.run(debug=True, host="0.0.0.0", port=puerto)
