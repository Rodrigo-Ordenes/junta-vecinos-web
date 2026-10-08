"""Sitio web de la Junta de Vecinos N.° 2 · Cerro Esperanza.

Aplicación Flask con SQLite. Incluye el sitio público (noticias, proyectos,
transparencia, documentos, sede vecinal con reservas, servicios comunales y
emprendimientos, y certificado de residencia) y un panel en español con cuatro
perfiles: administrador, coordinador (con permisos modulares), socio acreditado y
comisión revisora de cuentas. Los vecinos no socios consultan el sitio público,
pero no tienen cuenta.
"""

import json
import os
import calendar
import csv
import io
import re
import secrets
import sqlite3
import shutil
import unicodedata
from urllib.parse import quote
from datetime import date, datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    Response,
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
import padron
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
    base.INSTANCE_DIR, "comprobantes_privados"
)
ARCHIVO_PRIVADO_PREFIX = "privado-"
ARCHIVO_CERTIFICADO_PREFIX = "privado-cert-"
CATEGORIAS_PROTEGIDAS = ("Estatutos", "Actas de asamblea")
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
app.config["PRIVATE_UPLOAD_FOLDER"] = os.path.abspath(
    os.environ.get("PRIVATE_UPLOAD_FOLDER", CARPETA_COMPROBANTES_PRIVADOS)
)
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


solo_digitos = padron.solo_digitos
telefono_chile = padron.telefono_chile


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
    """Indica si el usuario puede entrar al módulo ``recurso`` del panel.

    El administrador entra a todo; el coordinador solo a los módulos cuyo permiso
    modular (columna ``permiso_*``) tiene activo; la comisión revisora solo al módulo
    de auditoría; el socio no entra al panel.
    """
    usuario = usuario or usuario_actual()
    if not usuario:
        return False
    if usuario["rol"] == "administrador":
        return True
    if usuario["rol"] == "coordinador":
        permiso = PERMISOS_COORDINADOR.get(recurso)
        return permiso == "panel" or bool(permiso and usuario[permiso])
    if usuario["rol"] == "comision_revisora":
        return recurso in {"panel", "auditorias"}
    return False


def puede_entrar_al_panel(usuario):
    return bool(usuario and usuario["rol"] in ("administrador", "coordinador", "comision_revisora"))


def login_requerido(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not usuario_actual():
            flash("Primero debes iniciar sesión.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


def requiere_permiso(modulo):
    """Deja pasar al administrador, o al coordinador con el flag del módulo activo.

    Sin sesión redirige al ingreso; sin permiso responde con una redirección limpia
    al panel (o al inicio si la cuenta no tiene panel) y un aviso.
    """
    def decorador(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            usuario = usuario_actual()
            if not usuario:
                flash("Primero debes iniciar sesión.", "error")
                return redirect(url_for("login", next=request.path))
            if not puede(modulo, usuario):
                flash("Tu perfil no tiene permiso para esta sección.", "error")
                if puede_entrar_al_panel(usuario):
                    return redirect(url_for("panel"))
                return redirect(url_for("mis_solicitudes"))
            return view(*args, **kwargs)
        return wrapper
    return decorador


permiso_requerido = requiere_permiso


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
            campo("imagen_url", "Fotografía o imagen del proyecto (opcional)", "imagen",
                  ayuda="Sube una foto JPG, PNG o WebP, o escribe un enlace directo."),
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
        "titulo": "Servicios comunales / Emprendimientos",
        "singular": "emprendimiento",
        "tabla": "servicios",
        "orden": "aprobado, nombre_servicio",
        "columnas": [("nombre_servicio", "Servicio"), ("vecino", "Responsable"), ("rubro", "Rubro"),
                     ("aprobado", "Publicado")],
        "campos": [
            campo("nombre_servicio", "Nombre del servicio", requerido=True),
            campo("vecino", "Socio o socia responsable", requerido=True),
            campo("rubro", "Rubro", "opciones", opciones=RUBROS_SERVICIO, requerido=True),
            campo("descripcion", "Descripción", "textarea"),
            campo("telefono", "Teléfono (+569…)"),
            campo("whatsapp", "WhatsApp (+569…)"),
            campo("email", "Correo"),
            campo("direccion", "Dirección"),
            campo("foto_url", "Fotografía o imagen del servicio (opcional)", "imagen",
                  ayuda="Sube una foto JPG, PNG o WebP, o escribe un enlace directo."),
            campo("aprobado", "Publicado en servicios comunales", "casilla"),
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
            campo("profesion", "Profesión, oficio u ocupación"),
            campo("periodo", "Período del cargo (ej: 2024 - 2027)"),
            campo("email", "Correo del cargo"),
            campo("telefono", "Teléfono"),
            campo("biografia", "Reseña o biografía detallada", "textarea",
                  ayuda="Información sobre su trayectoria comunitaria, objetivos y rol en la junta."),
            campo("foto_url", "Fotografía del integrante (opcional)", "imagen",
                  ayuda="Sube una foto JPG, PNG o WebP, o escribe un enlace directo."),
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
    "panel": "panel",
    "noticias": "permiso_noticias",
    "documentos": "permiso_noticias",
    "avance_proyectos": "permiso_noticias",
    "reservas": "permiso_reservas",
    "bloques": "permiso_reservas",
    "certificados": "permiso_certificados",
    "directorio": "permiso_directorio",
    "servicios": "permiso_directorio",
    "servicios_pendientes": "permiso_directorio",
    "socios": "permiso_socios",
    "usuarios_pendientes": "permiso_socios",
    "crear_socios": "permiso_socios",
    "cargar_padron": "permiso_socios",
    "censo": "permiso_socios",
}

# Casillas que el administrador asigna a cada coordinador: (columna, etiqueta, ayuda).
MODULOS_COORDINADOR = [
    ("permiso_noticias", "Noticias y documentos",
     "Publicar noticias, documentos y avances de proyectos."),
    ("permiso_reservas", "Reservas de la sede",
     "Aprobar, editar y crear reservas y actividades fijas."),
    ("permiso_certificados", "Certificados de residencia",
     "Revisar solicitudes y su documento de domicilio."),
    ("permiso_directorio", "Servicios comunales / Emprendimientos",
     "Aprobar y editar emprendimientos de los socios."),
    ("permiso_socios", "Padrón de socios",
     "Padrón, cuentas de socio, cumpleaños, carga masiva y censo infantil."),
]

MENU_PANEL = [
    ("Solicitudes", [
        ("reservas", "Reservas de la sede", "🗓️"),
        ("certificados", "Certificados de residencia", "📄"),
        ("servicios_pendientes", "Aprobar emprendimientos", "🧰"),
        ("servicios", "Servicios comunales / Emprendimientos", "🧰"),
        ("mensajes", "Mensajes de contacto", "✉️"),
    ]),
    ("Socios", [
        ("socios", "Padrón y cumpleaños", "👥"),
        ("cargar_padron", "Carga masiva del padrón", "📥"),
        ("crear_socios", "Crear cuenta de socio", "➕"),
        ("usuarios_pendientes", "Aprobar cuentas de socio", "🛡️"),
        ("censo", "Censo infantil (Navidad)", "🎁"),
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
        ("auditorias", "Informes de la Comisión Revisora", "📋"),
    ]),
    ("Configuración", [
        ("bloques", "Actividades fijas de la sede", "🏛️"),
        ("permisos", "Permisos de coordinación", "🛡️"),
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
    "crear_socios": "panel_crear_socio",
    "socios": "panel_socios",
    "cargar_padron": "panel_cargar_padron",
    "censo": "panel_censo",
    "permisos": "panel_permisos_coordinador",
    "servicios_pendientes": "panel_servicios_pendientes",
    "auditorias": "panel_auditorias",
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
    if puede_entrar_al_panel(usuario):
        if puede("reservas", usuario):
            pendientes += consultar(
                "SELECT COUNT(*) c FROM reservas WHERE estado = 'Pendiente'", uno=True
            )["c"]
        if puede("certificados", usuario):
            pendientes += consultar(
                "SELECT COUNT(*) c FROM certificados WHERE estado = 'Recibida'", uno=True
            )["c"]
        if puede("servicios_pendientes", usuario):
            pendientes += consultar(
                "SELECT COUNT(*) c FROM servicios WHERE aprobado = 0", uno=True
            )["c"]
        if puede("mensajes", usuario):
            pendientes += consultar(
                "SELECT COUNT(*) c FROM mensajes WHERE leido = 0", uno=True
            )["c"]
        if puede("usuarios_pendientes", usuario):
            pendientes += consultar(
                "SELECT COUNT(*) c FROM usuarios WHERE rol = 'socio' "
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
        "puede_entrar_al_panel": puede_entrar_al_panel(usuario),
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
    informes = []
    if usuario:
        informes = consultar(
            "SELECT i.*, u.nombre AS autor_nombre, u.apellidos AS autor_apellidos "
            "FROM informes_auditoria i JOIN usuarios u ON u.id = i.autor_id "
            "ORDER BY i.fecha_publicacion DESC, i.id DESC"
        )
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
        informes=informes,
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
        categorias_protegidas=CATEGORIAS_PROTEGIDAS,
    )


def documento_protegido(documento_id):
    """Estatutos y actas con archivo propio: solo se leen en pantalla."""
    marcas = ", ".join("?" for _ in CATEGORIAS_PROTEGIDAS)
    documento = consultar(
        f"SELECT * FROM documentos WHERE id = ? AND categoria IN ({marcas})",
        (documento_id, *CATEGORIAS_PROTEGIDAS), uno=True,
    )
    if not documento or not documento["archivo"] or es_archivo_privado(documento["archivo"]):
        abort(404)
    return documento


def tipo_de_visor(nombre_archivo):
    extension = nombre_archivo.rsplit(".", 1)[-1].lower() if "." in nombre_archivo else ""
    if extension == "pdf":
        return "pdf"
    if extension in {"jpg", "jpeg", "png", "webp", "gif"}:
        return "imagen"
    return "no_visible"


@app.route("/documentos/<int:documento_id>/ver")
def visor_documento(documento_id):
    documento = documento_protegido(documento_id)
    return render_template(
        "visor_documento.html", documento=documento,
        tipo_visor=tipo_de_visor(documento["archivo"]),
    )


@app.route("/documentos/<int:documento_id>/contenido")
def contenido_documento(documento_id):
    """Entrega el archivo para el visor embebido (sin descarga directa)."""
    documento = documento_protegido(documento_id)
    if request.headers.get("Sec-Fetch-Dest") == "document":
        # Abrir la dirección directamente en el navegador devuelve al visor.
        return redirect(url_for("visor_documento", documento_id=documento_id))
    respuesta = send_from_directory(
        carpeta_subidas(), documento["archivo"], as_attachment=False
    )
    respuesta.headers["Content-Disposition"] = "inline"
    respuesta.headers["Cache-Control"] = "private, no-store"
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["X-Frame-Options"] = "SAMEORIGIN"
    respuesta.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    return respuesta


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
        marcas = ", ".join("?" for _ in CATEGORIAS_PROTEGIDAS)
        if consultar(
            f"SELECT 1 FROM documentos WHERE archivo = ? AND categoria IN ({marcas})",
            (nombre, *CATEGORIAS_PROTEGIDAS), uno=True,
        ):
            # Estatutos y actas internas: sin descarga directa, solo visor.
            abort(404)
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


def obtener_disponibilidad_mes(anio, mes):
    """Devuelve la matriz mensual de días y bloques horarios para la reserva interactiva."""
    conf = obtener_config()
    apertura = conf.get("horario_sede_inicio", "09:00")
    cierre = conf.get("horario_sede_fin", "21:00")
    apertura_min = minutos_de_hora(apertura) or 540
    cierre_min = minutos_de_hora(cierre) or 1260
    hoy = fecha_hoy()
    ahora_santiago = datetime.now(ZONA_SANTIAGO)
    minuto_actual = ahora_santiago.hour * 60 + ahora_santiago.minute

    cal = calendar.Calendar(firstweekday=0)
    semanas_fechas = cal.monthdatescalendar(anio, mes)

    bloques_fijos = consultar(
        "SELECT * FROM bloques_fijos WHERE activo = 1 ORDER BY hora_inicio"
    )

    primer_dia = semanas_fechas[0][0].isoformat()
    ultimo_dia = semanas_fechas[-1][-1].isoformat()

    reservas = consultar(
        "SELECT * FROM reservas WHERE fecha BETWEEN ? AND ? AND estado IN ('Aprobada', 'Pendiente') ORDER BY hora_inicio",
        (primer_dia, ultimo_dia),
    )
    reservas_por_fecha = {}
    for r in reservas:
        reservas_por_fecha.setdefault(r["fecha"], []).append(r)

    semanas_resultado = []
    for semana in semanas_fechas:
        dias_res = []
        for dia in semana:
            dia_iso = dia.isoformat()
            pasado = dia < hoy
            es_hoy = dia == hoy
            en_mes = dia.month == mes

            acts = [
                {
                    "tipo": "fija",
                    "hora_inicio": b["hora_inicio"],
                    "hora_fin": b["hora_fin"],
                    "titulo": b["actividad"],
                }
                for b in bloques_fijos
                if b["dia_semana"] == dia.weekday()
            ]
            acts += [
                {
                    "tipo": "reserva",
                    "hora_inicio": r["hora_inicio"],
                    "hora_fin": r["hora_fin"],
                    "titulo": r["actividad"],
                }
                for r in reservas_por_fecha.get(dia_iso, [])
            ]

            libres = intervalos_disponibles(dia, acts, apertura, cierre) if not pasado else []

            # Crear bloques estándar (de 2 horas)
            slots = []
            duracion = 120
            cur = apertura_min
            while cur + 60 <= cierre_min:
                fin_cur = min(cur + duracion, cierre_min)
                h_ini = hora_desde_minutos(cur)
                h_fin = hora_desde_minutos(fin_cur)
                choca = False
                motivo = "Disponible"
                if pasado:
                    choca = True
                    motivo = "Fecha pasada"
                elif es_hoy and cur <= minuto_actual:
                    choca = True
                    motivo = "Hora pasada"
                elif hay_choque(dia, h_ini, h_fin):
                    choca = True
                    motivo = "Horario reservado"

                slots.append({
                    "hora_inicio": h_ini,
                    "hora_fin": h_fin,
                    "disponible": not choca,
                    "motivo": motivo,
                })
                cur = fin_cur

            # Incluir también los intervalos libres si no coinciden exactamente con los bloques
            for lib_ini, lib_fin in libres:
                if not any(s["hora_inicio"] == lib_ini and s["hora_fin"] == lib_fin for s in slots):
                    slots.append({
                        "hora_inicio": lib_ini,
                        "hora_fin": lib_fin,
                        "disponible": True,
                        "motivo": "Intervalo libre disponible",
                    })

            slots.sort(key=lambda s: s["hora_inicio"])
            total_disponibles = sum(1 for s in slots if s["disponible"])

            dias_res.append({
                "fecha": dia_iso,
                "dia": dia.day,
                "dia_semana": dia.weekday(),
                "nombre_dia": DIAS_SEMANA[dia.weekday()],
                "en_mes": en_mes,
                "hoy": es_hoy,
                "pasado": pasado,
                "disponible": bool(not pasado and total_disponibles > 0),
                "slots": slots,
                "slots_disponibles": total_disponibles,
                "intervalos_libres": libres,
                "total_actividades": len(acts),
            })
        semanas_resultado.append(dias_res)

    mes_ant = (date(anio, mes, 1) - timedelta(days=1))
    mes_sig = (date(anio, mes, 28) + timedelta(days=4)).replace(day=1)

    return {
        "anio": anio,
        "mes": mes,
        "mes_nombre": MESES[mes - 1].capitalize(),
        "mes_anterior": mes_ant.strftime("%Y-%m"),
        "mes_siguiente": mes_sig.strftime("%Y-%m"),
        "horario_inicio": apertura,
        "horario_fin": cierre,
        "semanas": semanas_resultado,
    }


@app.route("/api/sede/disponibilidad")
def api_sede_disponibilidad():
    mes_str = request.args.get("mes")
    ref = fecha_del_mes(mes_str) if mes_str else fecha_hoy()
    data = obtener_disponibilidad_mes(ref.year, ref.month)
    return jsonify(data)


@app.route("/sede/reservar", methods=["GET", "POST"])
def reservar():
    usuario = usuario_actual()
    conf = obtener_config()
    fecha_param = request.args.get("fecha", "")
    hora_ini_param = request.args.get("hora_inicio", "")
    hora_fin_param = request.args.get("hora_fin", "")
    datos = {
        "fecha": fecha_param,
        "hora_inicio": hora_ini_param,
        "hora_fin": hora_fin_param,
        "solicitante": usuario["nombre"] if usuario else "",
        "email": (usuario["email"] if usuario else "") or "",
        "telefono": "",
        "actividad": "",
        "personas": "",
        "observacion": "",
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

    ref_fecha = a_fecha(datos["fecha"]) or fecha_hoy()
    disponibilidad_mes = obtener_disponibilidad_mes(ref_fecha.year, ref_fecha.month)
    return render_template(
        "reservar.html",
        datos=datos,
        disponibilidad_mes=disponibilidad_mes,
        disponibilidad_json=json.dumps(disponibilidad_mes),
    )


# ---------------------------------------------------------------------------
# Servicios comunales / Emprendimientos
# ---------------------------------------------------------------------------
@app.route("/directorio")
@app.route("/servicios-comunales")
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


def pantalla_restringida(titulo, volver_a, mensaje=None):
    """Bloqueo visual para quien no tiene sesión de socio acreditado."""
    return render_template(
        "restringido.html", titulo_restringido=titulo, volver_a=volver_a,
        mensaje_restringido=mensaje,
    )


@app.route("/inscribir-servicio", methods=["GET", "POST"])
@app.route("/directorio/inscribir", methods=["GET", "POST"])
def inscribir_servicio():
    if not usuario_actual():
        respuesta = pantalla_restringida(
            "Inscribir un emprendimiento", url_for("inscribir_servicio"),
            mensaje="Para inscribir y promocionar tu emprendimiento en la plataforma comunitaria, debes ingresar con tu Cuenta de Socio acreditada."
        )
        return respuesta, (403 if request.method == "POST" else 200)

    datos = {}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip() for k in
                 ("nombre_servicio", "vecino", "rubro", "descripcion", "telefono",
                  "whatsapp", "email", "direccion")}
        telefono = telefono_chile(datos["telefono"])
        whatsapp = telefono_chile(datos["whatsapp"]) if datos["whatsapp"] else ""
        if not datos["nombre_servicio"] or not datos["vecino"] or not datos["telefono"]:
            flash("Completa el nombre del servicio, tu nombre y un teléfono.", "error")
        elif not telefono:
            flash("El teléfono debe ser un móvil chileno con el formato +569 y 8 dígitos.", "error")
        elif datos["whatsapp"] and not whatsapp:
            flash("El WhatsApp debe ser un móvil chileno con el formato +569 y 8 dígitos.", "error")
        else:
            subida = guardar_archivo("foto", extensiones={"jpg", "jpeg", "png", "webp"})
            foto_url = url_for("static", filename=f"uploads/{subida}") if subida else ""
            ejecutar(
                "INSERT INTO servicios (nombre_servicio, vecino, rubro, descripcion, telefono, "
                "whatsapp, email, direccion, foto_url, aprobado, fecha_solicitud) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (
                    datos["nombre_servicio"], datos["vecino"],
                    datos["rubro"] if datos["rubro"] in RUBROS_SERVICIO else "Otros",
                    datos["descripcion"], telefono,
                    solo_digitos(whatsapp), datos["email"], datos["direccion"],
                    foto_url,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            flash(
                "¡Gracias! Tu emprendimiento quedó registrado y aparecerá en Servicios "
                "Comunales una vez que la directiva lo revise.",
                "success",
            )
            return redirect(url_for("directorio"))
    else:
        usuario = usuario_actual()
        datos = {
            "vecino": " ".join(
                p for p in (usuario["nombre"], usuario["apellidos"]) if p
            ),
            "telefono": usuario["telefono"] or "",
        }
    return render_template("inscribir_servicio.html", rubros=RUBROS_SERVICIO, datos=datos)


# ---------------------------------------------------------------------------
# Certificado de residencia
# ---------------------------------------------------------------------------
def rut_con_cuenta(rut_crudo):
    """True si el RUT ya está asociado a una cuenta (activa, pendiente o del padrón)."""
    rut = padron.normalizar_rut(rut_crudo)
    candidatos = {c for c in (rut, (rut_crudo or "").strip()) if c}
    if not candidatos:
        return False
    marcas = ", ".join("?" for _ in candidatos)
    return consultar(
        f"SELECT 1 FROM usuarios WHERE rut IN ({marcas}) LIMIT 1", tuple(candidatos), uno=True
    ) is not None



@app.route("/certificado", methods=["GET", "POST"])
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
        elif crear_cuenta and rut_con_cuenta(datos["rut"]):
            flash(
                "Ya existe una cuenta o solicitud de socio con ese RUT. Ingresa con tu "
                "usuario o consulta a la directiva si no recuerdas tu acceso.",
                "error",
            )
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
                        "estado_aprobacion, fecha_creacion, rut, direccion, telefono) "
                        "VALUES (?, ?, ?, ?, 'socio', 0, 'Pendiente', ?, ?, ?, ?)",
                        (
                            datos["nombre"], nuevo_usuario, datos["email"],
                            generate_password_hash(nueva_clave),
                            datetime.now().isoformat(timespec="seconds"),
                            padron.normalizar_rut(datos["rut"]) or datos["rut"],
                            datos["direccion"], datos["telefono"],
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
                    "Tu solicitud de certificado fue recibida. La activación de tu cuenta "
                    "de socio quedó pendiente de validación contra el padrón oficial; "
                    "podrás ingresar cuando la directiva la apruebe.",
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


@app.route("/certificado/estado", methods=["GET", "POST"])
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


def hitos_de(texto):
    """Convierte las líneas «AAAA | texto» de la configuración en una línea de tiempo."""
    hitos = []
    for linea in (texto or "").splitlines():
        if "|" not in linea:
            continue
        anio, detalle = (parte.strip() for parte in linea.split("|", 1))
        if anio and detalle:
            hitos.append({"anio": anio, "texto": detalle})
    hitos.sort(key=lambda h: (int(h["anio"]) if h["anio"].isdigit() else 10**9))
    return hitos


@app.route("/historia")
def historia():
    conf = obtener_config()
    anio_fundacion = conf["anio_fundacion"]
    anios = (
        fecha_hoy().year - int(anio_fundacion) if anio_fundacion.isdigit() else None
    )
    return render_template(
        "historia.html", hitos=hitos_de(conf["hitos_historia"]),
        anios_de_historia=anios,
    )


@app.route("/como-asociarse")
def como_asociarse():
    return render_template("como_asociarse.html")


@app.route("/movilidad")
def movilidad():
    return render_template("movilidad.html")


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
RUTAS_SIN_CAMBIO_DE_CLAVE = {"static", "cambiar_clave", "logout"}


@app.before_request
def exigir_cambio_de_clave():
    """Las cuentas creadas por carga masiva deben cambiar su clave inicial."""
    if request.endpoint is None or request.endpoint in RUTAS_SIN_CAMBIO_DE_CLAVE:
        return None
    usuario = usuario_actual()
    if usuario and usuario["debe_cambiar_clave"]:
        return redirect(url_for("cambiar_clave"))
    return None


def destino_seguro(destino):
    return bool(destino) and destino.startswith("/") and not destino.startswith("//")


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
                flash(
                    "Tu cuenta de socio está pendiente de validación contra el padrón "
                    "oficial.", "info",
                )
            elif not fila["activo"]:
                flash("La cuenta está desactivada. Contacta a la directiva.", "error")
            else:
                session.clear()
                session["usuario_id"] = fila["id"]
                destino = request.args.get("next")
                if not destino_seguro(destino):
                    destino = (
                        url_for("panel") if puede_entrar_al_panel(fila)
                        else url_for("mis_solicitudes")
                    )
                return redirect(destino)
        else:
            flash("Usuario o clave incorrectos.", "error")
    return render_template("login.html")


@app.route("/registro", methods=["GET", "POST"])
def registro():
    """Solicitud de activación de cuenta de socio (se valida contra el padrón)."""
    datos = {}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip()
                 for k in ("nombre", "rut", "usuario", "email")}
        clave = request.form.get("clave") or ""
        confirmar_clave = request.form.get("confirmar_clave") or ""
        rut = padron.normalizar_rut(datos["rut"])
        if not datos["nombre"] or len(datos["usuario"]) < 3 or len(clave) < 8:
            flash(
                "Completa tu nombre, un usuario de al menos 3 caracteres "
                "y una clave de al menos 8 caracteres.",
                "error",
            )
        elif not rut:
            flash("Ingresa un RUT válido, por ejemplo 12.345.678-5.", "error")
        elif clave != confirmar_clave:
            flash("Las claves no coinciden.", "error")
        elif consultar("SELECT id FROM usuarios WHERE usuario = ?", (datos["usuario"],), uno=True):
            flash("Ese nombre de usuario ya existe, elige otro.", "error")
        elif rut_con_cuenta(rut):
            flash(
                "Ya existe una cuenta o solicitud con ese RUT. Si no recuerdas tu acceso, "
                "consulta a la directiva.",
                "error",
            )
        else:
            ejecutar(
                "INSERT INTO usuarios (nombre, usuario, email, clave_hash, rol, activo, "
                "estado_aprobacion, fecha_creacion, rut) "
                "VALUES (?, ?, ?, ?, 'socio', 0, 'Pendiente', ?, ?)",
                (datos["nombre"], datos["usuario"], datos["email"],
                 generate_password_hash(clave),
                 datetime.now().isoformat(timespec="seconds"), rut),
            )
            flash(
                "Recibimos tu solicitud de activación de cuenta de socio. La directiva "
                "la validará contra el padrón oficial antes de que puedas ingresar.",
                "info",
            )
            return redirect(url_for("login"))
    return render_template("registro.html", datos=datos)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/cuenta/clave", methods=["GET", "POST"])
@login_requerido
def cambiar_clave():
    usuario = usuario_actual()
    obligatorio = bool(usuario["debe_cambiar_clave"])
    if request.method == "POST":
        actual = request.form.get("clave_actual") or ""
        nueva = request.form.get("clave_nueva") or ""
        confirmar = request.form.get("confirmar_clave") or ""
        if not check_password_hash(usuario["clave_hash"], actual):
            flash("La clave actual no es correcta.", "error")
        elif len(nueva) < 8:
            flash("La clave nueva debe tener al menos 8 caracteres.", "error")
        elif nueva != confirmar:
            flash("Las claves nuevas no coinciden.", "error")
        elif nueva == actual:
            flash("Elige una clave distinta a la actual.", "error")
        elif usuario["rut"] and solo_digitos(nueva) == solo_digitos(usuario["rut"].split("-")[0]):
            flash("La clave no puede ser el número de tu RUT.", "error")
        else:
            ejecutar(
                "UPDATE usuarios SET clave_hash = ?, debe_cambiar_clave = 0 WHERE id = ?",
                (generate_password_hash(nueva), usuario["id"]),
            )
            flash("Tu clave fue actualizada.", "success")
            return redirect(
                url_for("panel") if puede_entrar_al_panel(usuario)
                else url_for("mis_solicitudes")
            )
    return render_template("cambiar_clave.html", obligatorio=obligatorio)


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
# Censo infantil (entrega de juguetes de Navidad)
# ---------------------------------------------------------------------------
EDAD_LIMITE_CENSO = 10
MAXIMO_MENORES_POR_SOCIO = 20


def fecha_corte_censo():
    """Las edades del censo se calculan al 25 de diciembre del año en curso."""
    return date(fecha_hoy().year, 12, 25)


def edad_en(nacimiento, referencia):
    f = a_fecha(nacimiento)
    if not f:
        return None
    return referencia.year - f.year - ((referencia.month, referencia.day) < (f.month, f.day))


def menores_del_censo(socio_id=None):
    """Menores de 10 años (al corte del censo) con los datos de su socio/a."""
    sql = (
        "SELECT m.*, u.nombre AS socio_nombre, u.apellidos AS socio_apellidos, "
        "u.rut AS socio_rut, u.numero_socio AS socio_numero, u.direccion AS socio_direccion, "
        "u.telefono AS socio_telefono FROM menores m JOIN usuarios u ON u.id = m.socio_id"
    )
    parametros = ()
    if socio_id is not None:
        sql += " WHERE m.socio_id = ?"
        parametros = (socio_id,)
    sql += " ORDER BY u.apellidos, u.nombre, m.fecha_nacimiento"
    corte = fecha_corte_censo()
    filas = []
    for fila in consultar(sql, parametros):
        edad = edad_en(fila["fecha_nacimiento"], corte)
        if edad is not None and 0 <= edad < EDAD_LIMITE_CENSO:
            filas.append({"datos": fila, "edad": edad})
    return filas


@app.route("/mis-menores", methods=["GET", "POST"])
@login_requerido
def mis_menores():
    usuario = usuario_actual()
    datos = {}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip()
                 for k in ("nombre", "rut", "fecha_nacimiento")}
        nacimiento = a_fecha(datos["fecha_nacimiento"])
        rut = padron.normalizar_rut(datos["rut"]) if datos["rut"] else ""
        total = consultar(
            "SELECT COUNT(*) c FROM menores WHERE socio_id = ?", (usuario["id"],), uno=True
        )["c"]
        if not datos["nombre"] or len(datos["nombre"]) > 120:
            flash("Ingresa el nombre del menor (hasta 120 caracteres).", "error")
        elif not nacimiento or nacimiento > fecha_hoy():
            flash("Ingresa una fecha de nacimiento válida.", "error")
        elif edad_en(nacimiento, fecha_corte_censo()) >= EDAD_LIMITE_CENSO:
            flash("El censo es solo para menores de 10 años.", "error")
        elif datos["rut"] and not rut:
            flash("El RUT del menor no es válido. Déjalo en blanco si no lo tiene.", "error")
        elif total >= MAXIMO_MENORES_POR_SOCIO:
            flash("Alcanzaste el máximo de menores registrados por socio.", "error")
        else:
            ejecutar(
                "INSERT INTO menores (socio_id, nombre, rut, fecha_nacimiento, fecha_registro) "
                "VALUES (?, ?, ?, ?, ?)",
                (usuario["id"], datos["nombre"], rut, nacimiento.isoformat(),
                 datetime.now().isoformat(timespec="seconds")),
            )
            flash("El menor quedó registrado en el censo infantil.", "success")
            return redirect(url_for("mis_menores"))
    return render_template(
        "mis_menores.html", menores=menores_del_censo(usuario["id"]), datos=datos,
        corte=fecha_corte_censo(),
    )


@app.route("/mis-menores/<int:menor_id>/eliminar", methods=["POST"])
@login_requerido
def mis_menores_eliminar(menor_id):
    usuario = usuario_actual()
    menor = consultar(
        "SELECT id FROM menores WHERE id = ? AND socio_id = ?", (menor_id, usuario["id"]),
        uno=True,
    )
    if not menor:
        abort(404)
    ejecutar("DELETE FROM menores WHERE id = ?", (menor_id,))
    flash("El registro fue eliminado.", "success")
    return redirect(url_for("mis_menores"))


def celda_segura(valor):
    """Neutraliza fórmulas al abrir el CSV en una planilla de cálculo."""
    texto = "" if valor is None else str(valor)
    if texto[:1] in ("=", "@", "\t", "\r"):
        return "'" + texto
    if texto[:1] in ("+", "-") and not re.fullmatch(r"[+\-]?[\d\s.\-]+", texto):
        return "'" + texto
    return texto


@app.route("/panel/censo-infantil")
@requiere_permiso("censo")
def panel_censo():
    return render_template(
        "panel/censo.html", menores=menores_del_censo(), corte=fecha_corte_censo()
    )


@app.route("/panel/censo-infantil/exportar")
@requiere_permiso("censo")
def panel_censo_exportar():
    salida = io.StringIO()
    escritor = csv.writer(salida, lineterminator="\r\n")
    escritor.writerow([
        "numero_socio", "socio", "rut_socio", "direccion", "telefono_socio",
        "menor", "rut_menor", "fecha_nacimiento", "edad_al_25_dic",
    ])
    for item in menores_del_censo():
        m = item["datos"]
        escritor.writerow([celda_segura(c) for c in (
            m["socio_numero"], f"{m['socio_nombre']} {m['socio_apellidos']}".strip(),
            m["socio_rut"], m["socio_direccion"], m["socio_telefono"],
            m["nombre"], m["rut"], m["fecha_nacimiento"], item["edad"],
        )])
    return Response(
        "\ufeff" + salida.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename=censo_infantil_{fecha_corte_censo().year}.csv"},
    )


# ---------------------------------------------------------------------------
# Padrón de socios, cumpleaños y carga masiva
# ---------------------------------------------------------------------------
def cumpleaneros_de(dia):
    """Socios vivos y activos que cumplen años en el día indicado."""
    return consultar(
        "SELECT id, nombre, apellidos, numero_socio, telefono, fecha_nacimiento "
        "FROM usuarios WHERE rol = 'socio' AND fallecido = 0 AND activo = 1 "
        "AND fecha_nacimiento IS NOT NULL AND fecha_nacimiento != '' "
        "AND strftime('%m-%d', fecha_nacimiento) = ? ORDER BY nombre, apellidos",
        (dia.strftime("%m-%d"),),
    )


def enlace_cumpleanos(socio):
    """Enlace de WhatsApp con el saludo institucional ('' si no hay móvil válido)."""
    telefono = telefono_chile(socio["telefono"])
    if not telefono:
        return ""
    texto = obtener_config()["mensaje_cumpleanos"].replace("{nombre}", socio["nombre"])
    return f"https://wa.me/{solo_digitos(telefono)}?text={quote(texto)}"


@app.route("/panel/socios")
@requiere_permiso("socios")
def panel_socios():
    busqueda = (request.args.get("q") or "").strip()
    sql = "FROM usuarios WHERE rol = 'socio'"
    parametros = []
    if busqueda:
        sql += (" AND (nombre LIKE ? OR apellidos LIKE ? OR rut LIKE ? "
                "OR numero_socio LIKE ? OR usuario LIKE ?)")
        parametros = [f"%{busqueda}%"] * 5
    total = consultar(f"SELECT COUNT(*) c {sql}", tuple(parametros), uno=True)["c"]
    socios = consultar(
        f"SELECT * {sql} ORDER BY apellidos, nombre LIMIT 500", tuple(parametros)
    )
    hoy = fecha_hoy()
    cumpleaneros = [
        {"datos": c, "edad": edad_en(c["fecha_nacimiento"], hoy),
         "whatsapp": enlace_cumpleanos(c)}
        for c in cumpleaneros_de(hoy)
    ]
    return render_template(
        "panel/socios.html", socios=socios, total=total, busqueda=busqueda,
        cumpleaneros=cumpleaneros, hoy=hoy,
    )


@app.route("/admin/cargar-padron", methods=["GET", "POST"])
@app.route("/panel/cargar-padron", methods=["GET", "POST"])
@requiere_permiso("cargar_padron")
def panel_cargar_padron():
    resultado = None
    if request.method == "POST":
        subido = request.files.get("archivo")
        if not subido or not subido.filename:
            flash("Selecciona el archivo .csv o .xlsx del padrón.", "error")
        else:
            simular = request.form.get("simular") == "on"
            try:
                registros = padron.leer_padron(subido.read(), subido.filename)
                resultado = padron.importar_padron(
                    registros, base.get_db(), simular=simular
                )
                resultado["simulado"] = simular
            except padron.ErrorPadron as error:
                flash(str(error), "error")
    return render_template(
        "panel/cargar_padron.html", resultado=resultado, campos=padron.CAMPOS
    )


# ---------------------------------------------------------------------------
# Panel de administración
# ---------------------------------------------------------------------------
@app.route("/panel")
@login_requerido
def panel():
    usuario = usuario_actual()
    if not puede_entrar_al_panel(usuario):
        return redirect(url_for("mis_solicitudes"))

    def contar(modulo, sql, parametros=()):
        if not puede(modulo, usuario):
            return None
        return consultar(sql, parametros, uno=True)["c"]

    resumen = {
        "reservas_pendientes": contar(
            "reservas", "SELECT COUNT(*) c FROM reservas WHERE estado = 'Pendiente'"),
        "certificados_pendientes": contar(
            "certificados",
            "SELECT COUNT(*) c FROM certificados WHERE estado IN ('Recibida', 'En revisión')"),
        "mensajes_nuevos": contar(
            "mensajes", "SELECT COUNT(*) c FROM mensajes WHERE leido = 0"),
        "servicios_por_aprobar": contar(
            "servicios_pendientes", "SELECT COUNT(*) c FROM servicios WHERE aprobado = 0"),
        "proyectos_en_ejecucion": contar(
            "avance_proyectos",
            "SELECT COUNT(*) c FROM proyectos WHERE horizonte = 'Actual' "
            "AND estado = 'En ejecución'"),
        "cuentas_por_aprobar": contar(
            "usuarios_pendientes",
            "SELECT COUNT(*) c FROM usuarios WHERE rol = 'socio' "
            "AND estado_aprobacion = 'Pendiente'"),
        "informes": contar(
            "auditorias", "SELECT COUNT(*) c FROM informes_auditoria"),
        "cumpleaneros_hoy": (
            len(cumpleaneros_de(fecha_hoy())) if puede("socios", usuario) else None
        ),
    }
    proximas = []
    if puede("reservas", usuario):
        proximas = consultar(
            "SELECT * FROM reservas WHERE fecha >= ? AND estado = 'Aprobada' "
            "ORDER BY fecha LIMIT 5", (fecha_hoy().isoformat(),)
        )
    return render_template("panel/inicio.html", resumen=resumen, proximas=proximas)


@app.route("/panel/usuarios-pendientes")
@requiere_permiso("usuarios_pendientes")
def panel_usuarios_pendientes():
    usuarios_pendientes = consultar(
        "SELECT id, nombre, usuario, email, rut, fecha_creacion FROM usuarios "
        "WHERE rol = 'socio' AND estado_aprobacion = 'Pendiente' ORDER BY fecha_creacion"
    )
    return render_template(
        "panel/usuarios_pendientes.html", usuarios_pendientes=usuarios_pendientes
    )


@app.route("/panel/usuarios/<int:usuario_id>/aprobar", methods=["POST"])
@requiere_permiso("usuarios_pendientes")
def panel_usuario_aprobar(usuario_id):
    usuario = consultar(
        "SELECT id FROM usuarios WHERE id = ? AND rol = 'socio' "
        "AND estado_aprobacion = 'Pendiente'",
        (usuario_id,), uno=True,
    )
    if not usuario:
        abort(404)
    ejecutar(
        "UPDATE usuarios SET estado_aprobacion = 'Aprobada', activo = 1 WHERE id = ?",
        (usuario_id,),
    )
    flash("La cuenta de socio fue validada y ya puede ingresar.", "success")
    return redirect(url_for("panel_usuarios_pendientes"))


@app.route("/panel/crear-socio", methods=["GET", "POST"])
@app.route("/panel/crear-vecino", methods=["GET", "POST"])
@requiere_permiso("crear_socios")
def panel_crear_socio():
    datos = {}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip()
                 for k in ("nombre", "apellidos", "rut", "numero_socio", "usuario", "email")}
        clave = request.form.get("clave") or ""
        confirmar_clave = request.form.get("confirmar_clave") or ""
        rut = padron.normalizar_rut(datos["rut"]) if datos["rut"] else ""
        if not datos["nombre"] or len(datos["usuario"]) < 3 or len(clave) < 8:
            flash("Ingresa el nombre, un usuario de 3 caracteres y una clave de 8 caracteres.", "error")
        elif datos["rut"] and not rut:
            flash("El RUT no es válido.", "error")
        elif clave != confirmar_clave:
            flash("Las claves no coinciden.", "error")
        elif consultar("SELECT id FROM usuarios WHERE usuario = ?", (datos["usuario"],), uno=True):
            flash("Ese nombre de usuario ya existe.", "error")
        elif datos["numero_socio"] and consultar(
            "SELECT id FROM usuarios WHERE numero_socio = ?", (datos["numero_socio"],), uno=True
        ):
            flash("Ese número de socio ya está registrado.", "error")
        elif rut and rut_con_cuenta(rut):
            flash("Ya existe una cuenta con ese RUT.", "error")
        else:
            actor = usuario_actual()
            aprobado = actor["rol"] == "administrador"
            ejecutar(
                "INSERT INTO usuarios (nombre, apellidos, usuario, email, clave_hash, rol, activo, "
                "estado_aprobacion, fecha_creacion, rut, numero_socio) "
                "VALUES (?, ?, ?, ?, ?, 'socio', ?, ?, ?, ?, ?)",
                (
                    datos["nombre"], datos["apellidos"], datos["usuario"], datos["email"],
                    generate_password_hash(clave), int(aprobado),
                    "Aprobada" if aprobado else "Pendiente",
                    datetime.now().isoformat(timespec="seconds"), rut, datos["numero_socio"],
                ),
            )
            if aprobado:
                flash("La cuenta de socio fue creada y quedó activa.", "success")
            else:
                flash("La cuenta quedó pendiente de aprobación administrativa.", "info")
            return redirect(url_for("panel_crear_socio"))
    return render_template("panel/crear_socio.html", datos=datos)


@app.route("/panel/permisos-coordinador", methods=["GET", "POST"])
@requiere_permiso("usuarios")
def panel_permisos_coordinador():
    """Solo el administrador asigna las casillas modulares de cada coordinador."""
    if request.method == "POST":
        coordinador = consultar(
            "SELECT id, nombre FROM usuarios WHERE id = ? AND rol = 'coordinador'",
            (request.form.get("usuario_id", type=int),), uno=True,
        )
        if not coordinador:
            abort(404)
        # Los nombres de columna salen de la constante MODULOS_COORDINADOR, nunca
        # del formulario; los valores viajan como parámetros.
        asignaciones = ", ".join(f"{columna} = ?" for columna, _, _ in MODULOS_COORDINADOR)
        valores = [1 if request.form.get(columna) == "on" else 0
                   for columna, _, _ in MODULOS_COORDINADOR]
        ejecutar(
            f"UPDATE usuarios SET {asignaciones} WHERE id = ?",
            (*valores, coordinador["id"]),
        )
        flash(f"Permisos de {coordinador['nombre']} actualizados.", "success")
        return redirect(url_for("panel_permisos_coordinador"))
    return render_template(
        "panel/permisos_coordinador.html",
        coordinadores=consultar(
            "SELECT * FROM usuarios WHERE rol = 'coordinador' ORDER BY nombre"
        ),
        modulos=MODULOS_COORDINADOR,
    )


# ---------------------------------------------------------------------------
# Comisión Revisora de Cuentas (Ley 19.418): informes inmutables
# ---------------------------------------------------------------------------
TIPOS_INFORME = ["Inventario", "Balance auditado", "Acta de revisión", "Informe de revisión"]


@app.route("/panel/auditorias")
@app.route("/panel/informes_auditoria")
@app.route("/panel/informes-auditoria")
@requiere_permiso("auditorias")
def panel_auditorias():
    informes = consultar(
        "SELECT i.*, u.nombre AS autor_nombre, u.apellidos AS autor_apellidos "
        "FROM informes_auditoria i JOIN usuarios u ON u.id = i.autor_id "
        "ORDER BY i.fecha_publicacion DESC, i.id DESC"
    )
    return render_template(
        "panel/auditorias.html", informes=informes,
        puede_subir=usuario_actual()["rol"] == "comision_revisora",
    )


@app.route("/panel/auditorias/nuevo", methods=["GET", "POST"])
@requiere_permiso("auditorias")
def panel_auditoria_nueva():
    usuario = usuario_actual()
    if usuario["rol"] != "comision_revisora":
        abort(403)
    datos = {}
    if request.method == "POST":
        datos = {k: (request.form.get(k) or "").strip()
                 for k in ("titulo", "descripcion", "tipo")}
        if not datos["titulo"] or datos["tipo"] not in TIPOS_INFORME:
            flash("Indica el título y el tipo de informe.", "error")
        elif not request.files.get("archivo") or not request.files["archivo"].filename:
            flash("Adjunta el documento del informe (PDF, Word, Excel o imagen).", "error")
        else:
            guardado = guardar_archivo(
                "archivo", privado=True,
                extensiones={"pdf", "doc", "docx", "xls", "xlsx", "jpg", "jpeg", "png"},
            )
            if guardado:
                ejecutar(
                    "INSERT INTO informes_auditoria (titulo, descripcion, tipo, archivo, "
                    "fecha_publicacion, autor_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (datos["titulo"], datos["descripcion"], datos["tipo"], guardado,
                     datetime.now().isoformat(timespec="seconds"), usuario["id"]),
                )
                flash(
                    "El informe quedó publicado en Transparencia. Por ley no puede "
                    "editarse ni eliminarse.", "success",
                )
                return redirect(url_for("panel_auditorias"))
    return render_template(
        "panel/auditoria_formulario.html", datos=datos, tipos=TIPOS_INFORME
    )


@app.route("/panel/auditorias/<int:informe_id>/editar", methods=["GET", "POST"])
@app.route("/panel/auditorias/<int:informe_id>/eliminar", methods=["GET", "POST"])
@login_requerido
def panel_auditoria_inmutable(informe_id):
    """Ni la administración ni la directiva pueden alterar informes de la comisión."""
    abort(403)


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
    flash("El emprendimiento fue aprobado y ya aparece en Servicios Comunales.", "success")
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


@app.route("/panel/reservas/<int:reserva_id>/editar", methods=["GET", "POST"])
@permiso_requerido("reservas")
def panel_reserva_editar(reserva_id):
    reserva = consultar("SELECT * FROM reservas WHERE id = ?", (reserva_id,), uno=True)
    if not reserva:
        abort(404)
    if request.method == "POST":
        datos = {
            k: (request.form.get(k) or "").strip()
            for k in (
                "fecha", "hora_inicio", "hora_fin", "actividad",
                "solicitante", "telefono", "email", "personas",
                "estado", "observacion",
            )
        }
        fecha = a_fecha(datos["fecha"])
        if not fecha or not datos["hora_inicio"] or not datos["hora_fin"] or not datos["actividad"] or not datos["solicitante"]:
            flash("Completa fecha, horarios, actividad y nombre del solicitante.", "error")
            return render_template("panel/reserva_editar.html", reserva=reserva, estados=ESTADOS_RESERVA)
        if datos["estado"] not in ESTADOS_RESERVA:
            flash("El estado seleccionado no es válido.", "error")
            return render_template("panel/reserva_editar.html", reserva=reserva, estados=ESTADOS_RESERVA)
        if hay_choque(fecha, datos["hora_inicio"], datos["hora_fin"], excluir_id=reserva_id):
            flash("Ese horario choca con otra actividad fija o reserva en la sede.", "error")
            return render_template("panel/reserva_editar.html", reserva=reserva, estados=ESTADOS_RESERVA)
        usuario = usuario_actual()
        ejecutar(
            "UPDATE reservas SET fecha = ?, hora_inicio = ?, hora_fin = ?, actividad = ?, "
            "solicitante = ?, telefono = ?, email = ?, personas = ?, estado = ?, observacion = ?, "
            "revisada_por = ? WHERE id = ?",
            (
                fecha.isoformat(), datos["hora_inicio"], datos["hora_fin"],
                datos["actividad"], datos["solicitante"], datos["telefono"],
                datos["email"], int(datos["personas"] or 0) or None,
                datos["estado"], datos["observacion"],
                usuario["nombre"], reserva_id,
            ),
        )
        flash("Los cambios en la reserva fueron guardados.", "success")
        return redirect(url_for("panel_reservas", estado=datos["estado"]))
    return render_template("panel/reserva_editar.html", reserva=reserva, estados=ESTADOS_RESERVA)


@app.route("/panel/reservas/<int:reserva_id>/eliminar", methods=["POST"])
@permiso_requerido("reservas")
def panel_reserva_eliminar(reserva_id):
    reserva = consultar("SELECT id FROM reservas WHERE id = ?", (reserva_id,), uno=True)
    if not reserva:
        abort(404)
    ejecutar("DELETE FROM reservas WHERE id = ?", (reserva_id,))
    flash("La reserva fue eliminada.", "success")
    return redirect(url_for("panel_reservas"))


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
    ("anio_fundacion", "Año de fundación de la organización", "texto"),
    ("hitos_historia", "Línea de tiempo (una línea por hito: AAAA | texto)", "textarea"),
    ("como_asociarse", "Cómo asociarse (texto para quienes aún no son socios)", "textarea"),
    ("mensaje_cumpleanos", "Saludo de cumpleaños (use {nombre})", "textarea"),
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
            if request.form.get("quitar_" + nombre):
                valores[nombre] = ""
            else:
                subida = guardar_archivo(
                    "imagen_archivo", extensiones={"jpg", "jpeg", "png", "webp"}
                )
                if subida:
                    valores[nombre] = url_for("static", filename=f"uploads/{subida}")
                else:
                    campo_texto = (request.form.get(nombre) or "").strip()
                    valores[nombre] = campo_texto if campo_texto else (fila_actual[nombre] if fila_actual else "")
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
    condiciones = []
    parametros = []

    if recurso == "documentos" and coordinador_actual():
        condiciones.append("categoria != 'Rendiciones de cuentas'")

    filtro_q = (request.args.get("q") or "").strip()
    filtro_rubro = (request.args.get("rubro") or "").strip()
    filtro_estado = (request.args.get("estado") or "").strip()
    filtro_periodo = (request.args.get("periodo") or "").strip()
    filtro_orden = (request.args.get("orden") or "recientes").strip()
    filtro_anio = (request.args.get("anio") or "").strip()

    if recurso == "servicios":
        if filtro_q:
            condiciones.append("(nombre_servicio LIKE ? OR vecino LIKE ? OR descripcion LIKE ?)")
            patron = f"%{filtro_q}%"
            parametros.extend([patron, patron, patron])
        if filtro_rubro:
            condiciones.append("rubro = ?")
            parametros.append(filtro_rubro)

    elif recurso == "proyectos":
        if filtro_q:
            condiciones.append("(nombre LIKE ? OR descripcion LIKE ? OR eje LIKE ?)")
            patron = f"%{filtro_q}%"
            parametros.extend([patron, patron, patron])
        if filtro_estado:
            condiciones.append("estado = ?")
            parametros.append(filtro_estado)

    elif recurso == "rendiciones":
        if filtro_q:
            condiciones.append("(titulo LIKE ? OR descripcion LIKE ? OR periodo LIKE ?)")
            patron = f"%{filtro_q}%"
            parametros.extend([patron, patron, patron])
        if filtro_periodo:
            condiciones.append("periodo = ?")
            parametros.append(filtro_periodo)

    elif recurso == "eventos":
        if filtro_q:
            condiciones.append("(nombre LIKE ? OR descripcion LIKE ?)")
            patron = f"%{filtro_q}%"
            parametros.extend([patron, patron])
        if filtro_anio:
            condiciones.append("strftime('%Y', fecha) = ?")
            parametros.append(filtro_anio)

    if condiciones:
        sql += " WHERE " + " AND ".join(condiciones)

    if recurso == "eventos" and filtro_orden == "antiguos":
        orden_sql = "fecha ASC"
    else:
        orden_sql = definicion["orden"]

    filas = consultar(f"{sql} ORDER BY {orden_sql}", tuple(parametros))

    contexto_extra = {
        "filtro_q": filtro_q,
    }

    if recurso == "servicios":
        contexto_extra["rubros"] = RUBROS_SERVICIO
        contexto_extra["filtro_rubro"] = filtro_rubro
    elif recurso == "proyectos":
        contexto_extra["estados"] = ESTADOS_PROYECTO
        contexto_extra["filtro_estado"] = filtro_estado
    elif recurso == "rendiciones":
        periodos_db = consultar(
            "SELECT DISTINCT periodo FROM rendiciones WHERE periodo IS NOT NULL AND periodo != '' ORDER BY periodo DESC"
        )
        contexto_extra["periodos"] = [p["periodo"] for p in periodos_db]
        contexto_extra["filtro_periodo"] = filtro_periodo
    elif recurso == "eventos":
        anios_db = consultar(
            "SELECT DISTINCT strftime('%Y', fecha) as anio FROM eventos WHERE fecha IS NOT NULL AND fecha != '' ORDER BY anio DESC"
        )
        contexto_extra["anios"] = [a["anio"] for a in anios_db if a["anio"]]
        contexto_extra["filtro_anio"] = filtro_anio
        contexto_extra["filtro_orden"] = filtro_orden

    return render_template(
        "panel/listado.html",
        recurso=recurso,
        definicion=definicion,
        filas=filas,
        dias_semana=DIAS_SEMANA,
        **contexto_extra
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
    try:
        ejecutar(f"DELETE FROM {definicion['tabla']} WHERE id = ?", (fila_id,))
    except sqlite3.IntegrityError:
        flash(
            "No se puede eliminar: el registro tiene información asociada "
            "(por ejemplo, informes de la Comisión Revisora). Desactívalo en su lugar.",
            "error",
        )
        return redirect(url_for("panel_listado", recurso=recurso))
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


@app.errorhandler(403)
def prohibido(e):
    return render_template("error.html", codigo=403,
                           mensaje="No tienes permiso para realizar esta acción. "
                                   "Los informes de la Comisión Revisora son inmutables."
                           if request.path.startswith("/panel/auditorias")
                           else "No tienes permiso para realizar esta acción."), 403


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
