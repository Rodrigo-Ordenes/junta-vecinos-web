"""Capa de datos del sitio de la Junta de Vecinos N.° 2 · Cerro Esperanza.

Usa SQLite a través del módulo estándar `sqlite3`, sin dependencias externas,
para que el sitio pueda instalarse en cualquier hosting básico.
"""

import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta

from flask import g
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.abspath(
    os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "instance"))
)
DB_PATH = os.path.join(INSTANCE_DIR, "junta.db")

DIAS_SEMANA = [
    "Lunes",
    "Martes",
    "Miércoles",
    "Jueves",
    "Viernes",
    "Sábado",
    "Domingo",
]

ROLES = ["administrador", "coordinador", "vecino"]

ESTADOS_PROYECTO = ["Postulado", "Aprobado", "En ejecución", "Finalizado", "Rechazado"]
ESTADOS_RESERVA = ["Pendiente", "Aprobada", "Rechazada", "Cancelada"]
ESTADOS_CERTIFICADO = [
    "Recibida",
    "En revisión",
    "Lista para retiro",
    "Entregada",
    "Rechazada",
]
CATEGORIAS_DOCUMENTO = [
    "Estatutos",
    "Leyes y normativa",
    "Actas de asamblea",
    "Rendiciones de cuentas",
    "Otros documentos",
]
RUBROS_SERVICIO = [
    "Alimentación",
    "Almacén y comercio",
    "Belleza y cuidado personal",
    "Construcción y oficios",
    "Cuidado de personas",
    "Educación y clases",
    "Salud",
    "Transporte y fletes",
    "Otros",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    usuario TEXT NOT NULL UNIQUE,
    email TEXT,
    clave_hash TEXT NOT NULL,
    rol TEXT NOT NULL DEFAULT 'vecino',
    activo INTEGER NOT NULL DEFAULT 1,
    estado_aprobacion TEXT NOT NULL DEFAULT 'Aprobada',
    fecha_creacion TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS noticias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    resumen TEXT NOT NULL,
    contenido TEXT NOT NULL,
    imagen_url TEXT DEFAULT '',
    fecha_evento TEXT,
    fecha_publicacion TEXT NOT NULL,
    publicado INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS proyectos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    descripcion TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'Postulado',
    eje TEXT DEFAULT '',
    fuente_financiamiento TEXT DEFAULT '',
    monto INTEGER,
    horizonte TEXT NOT NULL DEFAULT 'Actual',
    imagen_url TEXT DEFAULT '',
    fecha_actualizacion TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS avances_proyecto (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proyecto_id INTEGER NOT NULL,
    porcentaje INTEGER NOT NULL CHECK (porcentaje BETWEEN 0 AND 100),
    detalle TEXT NOT NULL,
    fecha_actualizacion TEXT NOT NULL,
    actualizado_por TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (proyecto_id) REFERENCES proyectos (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_avances_proyecto_proyecto_id
    ON avances_proyecto (proyecto_id, id DESC);

CREATE TABLE IF NOT EXISTS rendiciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    periodo TEXT NOT NULL,
    descripcion TEXT DEFAULT '',
    archivo TEXT DEFAULT '',
    fecha_publicacion TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS movimientos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rendicion_id INTEGER,
    evento_id INTEGER,
    fecha TEXT NOT NULL,
    concepto TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'Gasto',
    monto INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (rendicion_id) REFERENCES rendiciones (id) ON DELETE CASCADE,
    FOREIGN KEY (evento_id) REFERENCES eventos (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    fecha TEXT NOT NULL,
    descripcion TEXT DEFAULT '',
    publicado INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    categoria TEXT NOT NULL DEFAULT 'Otros documentos',
    descripcion TEXT DEFAULT '',
    archivo TEXT DEFAULT '',
    enlace TEXT DEFAULT '',
    fecha_publicacion TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bloques_fijos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dia_semana INTEGER NOT NULL,
    hora_inicio TEXT NOT NULL,
    hora_fin TEXT NOT NULL,
    actividad TEXT NOT NULL,
    responsable TEXT DEFAULT '',
    activo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS reservas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,
    hora_inicio TEXT NOT NULL,
    hora_fin TEXT NOT NULL,
    actividad TEXT NOT NULL,
    solicitante TEXT NOT NULL,
    telefono TEXT DEFAULT '',
    email TEXT DEFAULT '',
    personas INTEGER,
    estado TEXT NOT NULL DEFAULT 'Pendiente',
    observacion TEXT DEFAULT '',
    revisada_por TEXT DEFAULT '',
    fecha_solicitud TEXT NOT NULL,
    usuario_id INTEGER REFERENCES usuarios (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS servicios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_servicio TEXT NOT NULL,
    vecino TEXT NOT NULL,
    rubro TEXT NOT NULL DEFAULT 'Otros',
    descripcion TEXT DEFAULT '',
    telefono TEXT DEFAULT '',
    whatsapp TEXT DEFAULT '',
    email TEXT DEFAULT '',
    direccion TEXT DEFAULT '',
    foto_url TEXT DEFAULT '',
    aprobado INTEGER NOT NULL DEFAULT 0,
    fecha_solicitud TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS certificados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    rut TEXT NOT NULL,
    direccion TEXT NOT NULL,
    telefono TEXT DEFAULT '',
    email TEXT DEFAULT '',
    motivo TEXT DEFAULT '',
    estado TEXT NOT NULL DEFAULT 'Recibida',
    observacion TEXT DEFAULT '',
    fecha_solicitud TEXT NOT NULL,
    codigo_seguimiento TEXT NOT NULL DEFAULT '',
    usuario_id INTEGER REFERENCES usuarios (id) ON DELETE SET NULL,
    archivo_domicilio TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS mensajes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    email TEXT NOT NULL,
    telefono TEXT DEFAULT '',
    mensaje TEXT NOT NULL,
    leido INTEGER NOT NULL DEFAULT 0,
    fecha_envio TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS directiva (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    cargo TEXT NOT NULL,
    email TEXT DEFAULT '',
    telefono TEXT DEFAULT '',
    foto_url TEXT DEFAULT '',
    orden INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS config (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL DEFAULT ''
);
"""

REQUISITOS_CERTIFICADO_ANTERIORES = (
    "Cédula de identidad vigente del solicitante.\n"
    "Documento que acredite domicilio en el sector (boleta de servicios, contrato de "
    "arriendo u otro).\n"
    "Ser residente del territorio de la Junta de Vecinos N.° 2 Cerro Esperanza.\n"
    "La solicitud es revisada por la directiva y el certificado se firma en la sede."
)
REQUISITOS_CERTIFICADO_PENDIENTES = (
    "Adjunta un documento que permita revisar tu domicilio. La directiva confirmará "
    "si el antecedente es suficiente durante la revisión de la solicitud."
)
REQUISITOS_CERTIFICADO_SIN_ADJUNTOS = (
    "La directiva confirmará qué documentos presentar y cómo entregarlos durante la "
    "revisión. Este formulario no recibe archivos."
)


CONFIG_POR_DEFECTO = {
    "socios_inscritos": "2400",
    "mision": (
        "Representar y organizar a las vecinas y vecinos del Cerro Esperanza, "
        "gestionando soluciones concretas a las necesidades del barrio y promoviendo "
        "la participación de todas las edades."
    ),
    "vision": (
        "Ser una junta de vecinos cercana, transparente y participativa, con una sede "
        "activa durante toda la semana y una comunidad informada de lo que se hace con "
        "cada peso y cada proyecto."
    ),
    "historia": (
        "La Junta de Vecinos N.° 2 agrupa a las familias del Cerro Esperanza y trabaja "
        "desde hace décadas en la mejora de los espacios comunes, la seguridad y la vida "
        "comunitaria del sector."
    ),
    "direccion_sede": "Sede vecinal, Cerro Esperanza, Valparaíso",
    "horario_atencion": "Atención en sede: consultar en la sección Sede vecinal.",
    "email_contacto": "contacto@jv2esperanza.cl",
    "telefono_contacto": "",
    "whatsapp_grupo": "",
    "facebook": "",
    "instagram": "",
    "requisitos_certificado": REQUISITOS_CERTIFICADO_PENDIENTES,
    "aviso_privacidad": "",
    "aviso_uso": "",
    "horario_sede_inicio": "09:00",
    "horario_sede_fin": "21:00",
    "coordinador_crear_vecinos": "0",
    "imagen_sede_url": "/static/img/sede_comunitaria.jpg",
    "imagen_hero_url": "/static/img/hero_comunidad.jpg",
    "plan_maestro_intro": (
        "El plan maestro reúne lo que la junta de vecinos quiere lograr en el cerro: "
        "los proyectos que están en marcha hoy y los que se postularán en los próximos años."
    ),
}


# ---------------------------------------------------------------------------
# Conexión
# ---------------------------------------------------------------------------
def get_db():
    """Devuelve la conexión SQLite asociada a la petición actual."""
    if "db" not in g:
        os.makedirs(INSTANCE_DIR, exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def consultar(sql, parametros=(), uno=False):
    cur = get_db().execute(sql, parametros)
    filas = cur.fetchall()
    cur.close()
    if uno:
        return filas[0] if filas else None
    return filas


def ejecutar(sql, parametros=()):
    db = get_db()
    cur = db.execute(sql, parametros)
    db.commit()
    nuevo_id = cur.lastrowid
    cur.close()
    return nuevo_id


def obtener_config():
    filas = consultar("SELECT clave, valor FROM config")
    valores = dict(CONFIG_POR_DEFECTO)
    valores.update({f["clave"]: f["valor"] for f in filas})
    return valores


def guardar_config(clave, valor):
    ejecutar(
        "INSERT INTO config (clave, valor) VALUES (?, ?) "
        "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
        (clave, valor),
    )


# ---------------------------------------------------------------------------
# Creación e inicialización
# ---------------------------------------------------------------------------
def init_db(app):
    """Crea las tablas, actualiza el esquema si hace falta y carga datos de ejemplo."""
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        db.commit()
        _migrar_esquema()
        _crear_usuarios_iniciales(app)
        _crear_datos_ejemplo()
        close_db()


def _columnas_declaradas():
    """Lee del SCHEMA qué columnas debería tener cada tabla."""
    tablas = {}
    patron = re.compile(
        r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", re.DOTALL
    )
    for nombre, cuerpo in patron.findall(SCHEMA):
        columnas = []
        for linea in cuerpo.split("\n"):
            linea = linea.strip().rstrip(",")
            if not linea or linea.upper().startswith(("FOREIGN KEY", "PRIMARY KEY", "UNIQUE")):
                continue
            partes = linea.split(None, 1)
            if len(partes) == 2:
                columnas.append((partes[0], partes[1]))
        tablas[nombre] = columnas
    return tablas


def _tabla_existe(nombre):
    return consultar(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (nombre,), uno=True
    ) is not None


def _migrar_esquema():
    """Agrega a una base de datos antigua las columnas y tablas que le falten.

    Permite actualizar el sitio sin perder la información ya cargada.
    """
    db = get_db()
    for tabla, columnas in _columnas_cache().items():
        if not _tabla_existe(tabla):
            continue
        actuales = {f["name"] for f in consultar(f"PRAGMA table_info({tabla})")}
        for nombre, definicion in columnas:
            if nombre in actuales or "PRIMARY KEY" in definicion.upper():
                continue
            # SQLite no acepta NOT NULL sin valor por defecto al agregar una columna.
            limpia = definicion
            if "NOT NULL" in limpia.upper() and "DEFAULT" not in limpia.upper():
                limpia = re.sub(r"\s*NOT NULL", "", limpia, flags=re.IGNORECASE)
            db.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {limpia}")
            db.commit()

    codigos = {
        fila["codigo_seguimiento"]
        for fila in consultar(
            "SELECT codigo_seguimiento FROM certificados WHERE codigo_seguimiento != ''"
        )
    }
    for fila in consultar(
        "SELECT id FROM certificados WHERE codigo_seguimiento IS NULL OR codigo_seguimiento = ''"
    ):
        codigo = secrets.token_hex(12).upper()
        while codigo in codigos:
            codigo = secrets.token_hex(12).upper()
        db.execute(
            "UPDATE certificados SET codigo_seguimiento = ? WHERE id = ?",
            (codigo, fila["id"]),
        )
        codigos.add(codigo)
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_certificados_codigo_seguimiento "
        "ON certificados (codigo_seguimiento) WHERE codigo_seguimiento != ''"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_certificados_usuario_id ON certificados (usuario_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_reservas_usuario_id ON reservas (usuario_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_usuarios_aprobacion "
        "ON usuarios (rol, estado_aprobacion)"
    )
    db.commit()

    db.execute(
        "UPDATE config SET valor = ? WHERE clave = 'requisitos_certificado' AND valor = ?",
        (REQUISITOS_CERTIFICADO_PENDIENTES, REQUISITOS_CERTIFICADO_ANTERIORES),
    )
    db.execute(
        "UPDATE config SET valor = ? WHERE clave = 'requisitos_certificado' AND valor = ?",
        (REQUISITOS_CERTIFICADO_PENDIENTES, REQUISITOS_CERTIFICADO_SIN_ADJUNTOS),
    )
    db.commit()

    # Actualizar imágenes iniciales si no tienen una asignada
    imagenes_noticias = {
        "Asamblea ordinaria de socias y socios": "/static/img/noticia_asamblea.jpg",
        "Comienza el mejoramiento de la plaza del cerro": "/static/img/noticia_plaza.jpg",
        "Taller de alfabetización digital para adultos mayores": "/static/img/noticia_taller.jpg",
    }
    for tit, img in imagenes_noticias.items():
        db.execute(
            "UPDATE noticias SET imagen_url = ? WHERE titulo = ? AND (imagen_url IS NULL OR imagen_url = '')",
            (img, tit),
        )

    imagenes_proyectos = {
        "Mejoramiento de la plaza central": "/static/img/noticia_plaza.jpg",
        "Cámaras de seguridad comunitarias": "/static/img/proyecto_seguridad.jpg",
        "Ampliación de la sede vecinal": "/static/img/proyecto_sede.jpg",
    }
    for nom, img in imagenes_proyectos.items():
        db.execute(
            "UPDATE proyectos SET imagen_url = ? WHERE nombre = ? AND (imagen_url IS NULL OR imagen_url = '')",
            (img, nom),
        )

    imagenes_directiva = {
        "María Elena Cabello Montesinos": "/static/img/directiva_presidenta.jpg",
        "Rosa Martínez Morales": "/static/img/directiva_presidenta.jpg",
        "José Miguel Saldaña Gaete": "/static/img/directiva_secretario.jpg",
        "Carlos Silva Araya": "/static/img/directiva_secretario.jpg",
        "Tesorero/a de la junta": "/static/img/directiva_tesorera.jpg",
        "Elena Soto Muñoz": "/static/img/directiva_tesorera.jpg",
    }
    for nom, img in imagenes_directiva.items():
        db.execute(
            "UPDATE directiva SET foto_url = ? WHERE nombre = ? AND (foto_url IS NULL OR foto_url = '')",
            (img, nom),
        )

    imagenes_servicios = {
        "Amasandería Doña Rosa": "/static/img/servicio_panaderia.jpg",
        "Costurería y arreglos de ropa": "/static/img/servicio_costura.jpg",
        "Costurería Ana": "/static/img/servicio_costura.jpg",
    }
    for nom, img in imagenes_servicios.items():
        db.execute(
            "UPDATE servicios SET foto_url = ? WHERE nombre_servicio = ? AND (foto_url IS NULL OR foto_url = '')",
            (img, nom),
        )

    db.execute(
        "INSERT INTO config (clave, valor) VALUES ('imagen_sede_url', '/static/img/sede_comunitaria.jpg') "
        "ON CONFLICT(clave) DO NOTHING"
    )
    db.execute(
        "INSERT INTO config (clave, valor) VALUES ('imagen_hero_url', '/static/img/hero_comunidad.jpg') "
        "ON CONFLICT(clave) DO NOTHING"
    )
    db.commit()

    # La versión anterior guardaba estos datos en otras tablas.
    if _tabla_existe("movimientos_rendicion") and not consultar(
        "SELECT id FROM movimientos LIMIT 1", uno=True
    ):
        db.execute(
            "INSERT INTO movimientos (rendicion_id, fecha, concepto, tipo, monto) "
            "SELECT rendicion_id, fecha, concepto, tipo, monto FROM movimientos_rendicion"
        )
        db.commit()
    if _tabla_existe("mensajes_contacto") and not consultar(
        "SELECT id FROM mensajes LIMIT 1", uno=True
    ):
        db.execute(
            "INSERT INTO mensajes (nombre, email, telefono, mensaje, leido, fecha_envio) "
            "SELECT nombre, email, telefono, mensaje, leido, fecha_envio FROM mensajes_contacto"
        )
        db.commit()
    if _tabla_existe("rendiciones"):
        columnas = {f["name"] for f in consultar("PRAGMA table_info(rendiciones)")}
        if "archivo_url" in columnas and "archivo" in columnas:
            db.execute(
                "UPDATE rendiciones SET archivo = COALESCE(archivo_url, '') "
                "WHERE (archivo IS NULL OR archivo = '') AND archivo_url IS NOT NULL"
            )
            db.commit()

    # Valores que no pueden quedar nulos tras agregar una columna.
    ahora = datetime.now().isoformat(timespec="seconds")
    for tabla, columna in (
        ("noticias", "fecha_publicacion"),
        ("proyectos", "fecha_actualizacion"),
        ("rendiciones", "fecha_publicacion"),
        ("documentos", "fecha_publicacion"),
    ):
        if _tabla_existe(tabla):
            columnas = {f["name"] for f in consultar(f"PRAGMA table_info({tabla})")}
            if columna in columnas:
                db.execute(f"UPDATE {tabla} SET {columna} = ? WHERE {columna} IS NULL", (ahora,))
                db.commit()
    for tabla, columna, valor in (
        ("proyectos", "horizonte", "Actual"),
        ("proyectos", "eje", ""),
        ("proyectos", "estado", "Postulado"),
    ):
        if _tabla_existe(tabla):
            columnas = {f["name"] for f in consultar(f"PRAGMA table_info({tabla})")}
            if columna in columnas:
                db.execute(
                    f"UPDATE {tabla} SET {columna} = ? WHERE {columna} IS NULL OR {columna} = ''",
                    (valor,),
                )
                db.commit()


_CACHE_COLUMNAS = {}


def _columnas_cache():
    if not _CACHE_COLUMNAS:
        _CACHE_COLUMNAS.update(_columnas_declaradas())
    return _CACHE_COLUMNAS


def _crear_usuarios_iniciales(app):
    if consultar("SELECT id FROM usuarios LIMIT 1", uno=True):
        return
    ahora = datetime.now().isoformat(timespec="seconds")
    admin_user = app.config.get("ADMIN_USER", "admin")
    admin_pass = app.config.get("ADMIN_PASSWORD", "esperanza2026")
    coord_user = app.config.get("COORD_USER", "coordinador")
    coord_pass = app.config.get("COORD_PASSWORD", "esperanza2026")
    ejecutar(
        "INSERT INTO usuarios (nombre, usuario, email, clave_hash, rol, activo, fecha_creacion) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (
            "Administrador/a de la junta",
            admin_user,
            "",
            generate_password_hash(admin_pass),
            "administrador",
            ahora,
        ),
    )
    ejecutar(
        "INSERT INTO usuarios (nombre, usuario, email, clave_hash, rol, activo, fecha_creacion) "
        "VALUES (?, ?, ?, ?, ?, 1, ?)",
        (
            "Encargado/a de la sede",
            coord_user,
            "",
            generate_password_hash(coord_pass),
            "coordinador",
            ahora,
        ),
    )


def _crear_datos_ejemplo():
    if consultar("SELECT id FROM noticias LIMIT 1", uno=True):
        return

    ahora = datetime.now().isoformat(timespec="seconds")
    hoy = date.today()

    noticias = [
        (
            "Asamblea ordinaria de socias y socios",
            "Convocamos a toda la comunidad a la asamblea ordinaria en la sede vecinal.",
            "Se realizará la asamblea ordinaria en la sede vecinal. Se revisará el estado de "
            "los proyectos, la rendición de cuentas del período y el uso de la sede durante el año.",
            "/static/img/noticia_asamblea.jpg",
            (hoy + timedelta(days=20)).isoformat(),
        ),
        (
            "Comienza el mejoramiento de la plaza del cerro",
            "Parte la primera etapa del proyecto de mejoramiento del espacio público.",
            "Gracias al proyecto aprobado por el Fondo de Desarrollo Vecinal comienzan los "
            "trabajos de mejoramiento de la plaza central: juegos infantiles, áreas verdes e "
            "iluminación.",
            "/static/img/noticia_plaza.jpg",
            None,
        ),
        (
            "Taller de alfabetización digital para adultos mayores",
            "Aprende a usar el celular, WhatsApp y esta misma página web.",
            "Todos los lunes, junto al grupo de adulto mayor, realizaremos un taller para "
            "aprender a usar el celular, enviar mensajes y revisar la información de la junta "
            "de vecinos en esta página.",
            "/static/img/noticia_taller.jpg",
            (hoy + timedelta(days=7)).isoformat(),
        ),
    ]
    for titulo, resumen, contenido, imagen_url, fecha_evento in noticias:
        ejecutar(
            "INSERT INTO noticias (titulo, resumen, contenido, imagen_url, fecha_evento, "
            "fecha_publicacion, publicado) VALUES (?, ?, ?, ?, ?, ?, 1)",
            (titulo, resumen, contenido, imagen_url, fecha_evento, ahora),
        )

    proyectos = [
        (
            "Mejoramiento de la plaza central",
            "Renovación de juegos infantiles, áreas verdes e iluminación de la plaza principal.",
            "En ejecución",
            "Espacios públicos",
            "Fondo de Desarrollo Vecinal (FONDEVE)",
            8500000,
            "Actual",
            "/static/img/noticia_plaza.jpg",
        ),
        (
            "Cámaras de seguridad comunitarias",
            "Postulación para instalar cámaras en los accesos principales del cerro.",
            "Postulado",
            "Seguridad",
            "Fondo Nacional de Seguridad Pública",
            4200000,
            "Actual",
            "/static/img/proyecto_seguridad.jpg",
        ),
        (
            "Ampliación de la sede vecinal",
            "Ampliación de la sede para realizar más actividades comunitarias en paralelo.",
            "Aprobado",
            "Sede vecinal",
            "Municipalidad",
            15000000,
            "Actual",
            "/static/img/proyecto_sede.jpg",
        ),
        (
            "Internet comunitario en la sede",
            "Instalación de banda ancha en la sede para talleres, trámites en línea y uso "
            "comunitario del computador.",
            "Postulado",
            "Conectividad",
            "Por definir",
            900000,
            "Actual",
            "",
        ),
        (
            "Escaleras y pasamanos del cerro",
            "Mejoramiento de escaleras y pasamanos en los sectores de mayor pendiente.",
            "Postulado",
            "Accesibilidad",
            "Por definir",
            6000000,
            "Futuro",
            "",
        ),
        (
            "Techado del patio de la sede",
            "Techar el patio para permitir actividades durante todo el año.",
            "Postulado",
            "Sede vecinal",
            "Por definir",
            3500000,
            "Futuro",
            "",
        ),
    ]
    for nombre, desc, estado, eje, fuente, monto, horizonte, imagen_url in proyectos:
        ejecutar(
            "INSERT INTO proyectos (nombre, descripcion, estado, eje, fuente_financiamiento, "
            "monto, horizonte, imagen_url, fecha_actualizacion) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (nombre, desc, estado, eje, fuente, monto, horizonte, imagen_url, ahora),
        )

    # Rendición de cuentas anual con sus movimientos
    rendicion_id = ejecutar(
        "INSERT INTO rendiciones (titulo, periodo, descripcion, archivo, fecha_publicacion) "
        "VALUES (?, ?, ?, '', ?)",
        (
            "Rendición de cuentas anual",
            str(hoy.year - 1),
            "Balance de ingresos y gastos de la junta de vecinos durante el año.",
            ahora,
        ),
    )
    movimientos = [
        (date(hoy.year - 1, 1, 15), "Aporte municipal FONDEVE", "Ingreso", 8500000),
        (date(hoy.year - 1, 12, 1), "Cuotas sociales del año", "Ingreso", 1200000),
        (date(hoy.year - 1, 3, 10), "Materiales mejoramiento de plaza", "Gasto", 3200000),
        (date(hoy.year - 1, 6, 5), "Electricidad y agua de la sede", "Gasto", 450000),
        (date(hoy.year - 1, 8, 20), "Insumos actividad Día del Niño", "Gasto", 280000),
    ]
    for fecha, concepto, tipo, monto in movimientos:
        ejecutar(
            "INSERT INTO movimientos (rendicion_id, fecha, concepto, tipo, monto) "
            "VALUES (?, ?, ?, ?, ?)",
            (rendicion_id, fecha.isoformat(), concepto, tipo, monto),
        )

    # Balance por actividad realizada
    eventos = [
        (
            "Bingo solidario de invierno",
            date(hoy.year, 6, 21),
            "Actividad para reunir fondos destinados a la mantención de la sede.",
            [
                ("Venta de cartones de bingo", "Ingreso", 620000),
                ("Aportes de vecinos y vecinas", "Ingreso", 150000),
                ("Premios y regalos", "Gasto", 210000),
                ("Colaciones y bebidas", "Gasto", 135000),
            ],
        ),
        (
            "Celebración Día del Niño",
            date(hoy.year, 8, 10),
            "Tarde recreativa para los niños y niñas del cerro.",
            [
                ("Aporte municipal", "Ingreso", 300000),
                ("Rifa comunitaria", "Ingreso", 180000),
                ("Juegos inflables", "Gasto", 240000),
                ("Golosinas y once", "Gasto", 190000),
            ],
        ),
    ]
    for nombre, fecha, descripcion, movs in eventos:
        evento_id = ejecutar(
            "INSERT INTO eventos (nombre, fecha, descripcion, publicado) VALUES (?, ?, ?, 1)",
            (nombre, fecha.isoformat(), descripcion),
        )
        for concepto, tipo, monto in movs:
            ejecutar(
                "INSERT INTO movimientos (evento_id, fecha, concepto, tipo, monto) "
                "VALUES (?, ?, ?, ?, ?)",
                (evento_id, fecha.isoformat(), concepto, tipo, monto),
            )

    # Documentos
    documentos = [
        ("Estatutos de la Junta de Vecinos N.° 2", "Estatutos",
         "Estatutos vigentes de la organización comunitaria."),
        ("Ley 19.418 sobre Juntas de Vecinos", "Leyes y normativa",
         "Ley que regula a las juntas de vecinos y demás organizaciones comunitarias."),
        ("Acta de la última asamblea", "Actas de asamblea",
         "Resumen de los acuerdos tomados en la última asamblea."),
    ]
    for titulo, categoria, descripcion in documentos:
        ejecutar(
            "INSERT INTO documentos (titulo, categoria, descripcion, archivo, enlace, "
            "fecha_publicacion) VALUES (?, ?, ?, '', '', ?)",
            (titulo, categoria, descripcion, ahora),
        )

    # Bloques fijos de la sede (0 = lunes ... 6 = domingo)
    bloques = [
        (0, "16:00", "18:30", "Grupo de adulto mayor", "Directiva"),
        (1, "19:00", "20:30", "Taller de karate", "Profesor de karate"),
        (3, "19:00", "20:30", "Taller de karate", "Profesor de karate"),
    ]
    for dia, inicio, fin, actividad, responsable in bloques:
        ejecutar(
            "INSERT INTO bloques_fijos (dia_semana, hora_inicio, hora_fin, actividad, "
            "responsable, activo) VALUES (?, ?, ?, ?, ?, 1)",
            (dia, inicio, fin, actividad, responsable),
        )

    # Reservas de ejemplo (una aprobada y una pendiente este fin de semana)
    dias_hasta_sabado = (5 - hoy.weekday()) % 7
    sabado = hoy + timedelta(days=dias_hasta_sabado or 7)
    domingo = sabado + timedelta(days=1)
    ejecutar(
        "INSERT INTO reservas (fecha, hora_inicio, hora_fin, actividad, solicitante, telefono, "
        "email, personas, estado, observacion, revisada_por, fecha_solicitud) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)",
        (
            sabado.isoformat(), "15:00", "20:00", "Cumpleaños familiar",
            "Familia Rojas", "+56 9 1234 5678", "", 40, "Aprobada", "Encargado/a de la sede", ahora,
        ),
    )
    ejecutar(
        "INSERT INTO reservas (fecha, hora_inicio, hora_fin, actividad, solicitante, telefono, "
        "email, personas, estado, observacion, revisada_por, fecha_solicitud) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?)",
        (
            domingo.isoformat(), "11:00", "14:00", "Reunión del club deportivo",
            "Club Deportivo Esperanza", "+56 9 8765 4321", "", 25, "Pendiente", ahora,
        ),
    )

    # Directorio de servicios de vecinas y vecinos
    servicios = [
        ("Amasandería Doña Rosa", "Rosa Fuentes", "Alimentación",
         "Pan amasado, empanadas y pasteles por encargo.", "+56 9 1111 1111", "56911111111"),
        ("Gasfitería Don Luis", "Luis Pérez", "Construcción y oficios",
         "Reparaciones de gasfitería y mantención de cañerías.", "+56 9 2222 2222", "56922222222"),
        ("Peluquería Carmen", "Carmen Reyes", "Belleza y cuidado personal",
         "Cortes de pelo a domicilio para toda la familia.", "+56 9 3333 3333", "56933333333"),
        ("Fletes El Cerro", "Pedro Sánchez", "Transporte y fletes",
         "Fletes y mudanzas dentro del cerro y la ciudad.", "+56 9 4444 4444", "56944444444"),
    ]
    for nombre, vecino, rubro, descripcion, telefono, whatsapp in servicios:
        ejecutar(
            "INSERT INTO servicios (nombre_servicio, vecino, rubro, descripcion, telefono, "
            "whatsapp, email, direccion, aprobado, fecha_solicitud) "
            "VALUES (?, ?, ?, ?, ?, ?, '', '', 1, ?)",
            (nombre, vecino, rubro, descripcion, telefono, whatsapp, ahora),
        )

    # Directiva
    directiva = [
        ("María Elena Cabello Montesinos", "Presidenta", "presidencia@jv2esperanza.cl", "/static/img/directiva_presidenta.jpg", 1),
        ("José Miguel Saldaña Gaete", "Secretario", "secretaria@jv2esperanza.cl", "/static/img/directiva_secretario.jpg", 2),
        ("Tesorero/a de la junta", "Tesorería", "tesoreria@jv2esperanza.cl", "/static/img/directiva_tesorera.jpg", 3),
        ("Encargado/a de la sede", "Coordinación de sede", "sede@jv2esperanza.cl", "", 4),
        ("Delegado/a de seguridad", "Seguridad", "seguridad@jv2esperanza.cl", "", 5),
    ]
    for nombre, cargo, email, foto_url, orden in directiva:
        ejecutar(
            "INSERT INTO directiva (nombre, cargo, email, telefono, foto_url, orden) "
            "VALUES (?, ?, ?, '', ?, ?)",
            (nombre, cargo, email, foto_url, orden),
        )

    for clave, valor in CONFIG_POR_DEFECTO.items():
        ejecutar(
            "INSERT INTO config (clave, valor) VALUES (?, ?) "
            "ON CONFLICT(clave) DO NOTHING",
            (clave, valor),
        )
