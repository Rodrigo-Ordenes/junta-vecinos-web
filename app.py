import os
from datetime import datetime, date
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_admin import Admin, AdminIndexView, expose
from flask_admin.contrib.sqla import ModelView
from flask_admin.theme import Bootstrap4Theme

from models import (
    db,
    Noticia,
    Proyecto,
    RendicionCuenta,
    MovimientoRendicion,
    MensajeContacto,
    DirectivaMiembro,
    ESTADO_PROYECTO_CHOICES,
)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "instance", "junta.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Usuario/clave del panel de administración (cámbialos antes de publicar el sitio)
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "esperanza2026")

db.init_app(app)


# ---------------------------------------------------------------------------
# Autenticación simple para el panel de administración
# ---------------------------------------------------------------------------
def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped


class SecureAdminIndexView(AdminIndexView):
    @expose("/")
    def index(self):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return super().index()


class SecureModelView(ModelView):
    def is_accessible(self):
        return session.get("logged_in", False)

    def inaccessible_callback(self, name, **kwargs):
        return redirect(url_for("login", next=request.url))


admin = Admin(
    app,
    name="Junta de Vecinos N.° 2 · Cerro Esperanza",
    theme=Bootstrap4Theme(),
    index_view=SecureAdminIndexView(name="Inicio"),
)
class RendicionCuentaAdminView(SecureModelView):
    inline_models = (MovimientoRendicion,)
    column_list = ("periodo", "titulo", "total_ingresos", "total_gastos", "saldo")


admin.add_view(SecureModelView(Noticia, db.session, name="Noticias y Actividades"))
admin.add_view(SecureModelView(Proyecto, db.session, name="Proyectos"))
admin.add_view(RendicionCuentaAdminView(RendicionCuenta, db.session, name="Rendiciones de Cuentas"))
admin.add_view(SecureModelView(DirectivaMiembro, db.session, name="Directiva"))
admin.add_view(SecureModelView(MensajeContacto, db.session, name="Mensajes de Contacto"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("usuario", "")
        pw = request.form.get("clave", "")
        if user == ADMIN_USER and pw == ADMIN_PASSWORD:
            session["logged_in"] = True
            next_url = request.args.get("next") or url_for("admin.index")
            return redirect(next_url)
        flash("Usuario o clave incorrectos.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Páginas públicas
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    ultimas_noticias = (
        Noticia.query.filter_by(publicado=True)
        .order_by(Noticia.fecha_publicacion.desc())
        .limit(3)
        .all()
    )
    proyectos_destacados = Proyecto.query.order_by(Proyecto.fecha_actualizacion.desc()).limit(3).all()
    return render_template("index.html", noticias=ultimas_noticias, proyectos=proyectos_destacados)


@app.route("/noticias")
def noticias():
    todas = (
        Noticia.query.filter_by(publicado=True)
        .order_by(Noticia.fecha_publicacion.desc())
        .all()
    )
    return render_template("noticias.html", noticias=todas)


@app.route("/noticias/<int:noticia_id>")
def noticia_detalle(noticia_id):
    noticia = Noticia.query.get_or_404(noticia_id)
    return render_template("noticia_detalle.html", noticia=noticia)


@app.route("/proyectos")
def proyectos():
    filtro_estado = request.args.get("estado")
    query = Proyecto.query
    if filtro_estado and filtro_estado in ESTADO_PROYECTO_CHOICES:
        query = query.filter_by(estado=filtro_estado)
    lista = query.order_by(Proyecto.fecha_actualizacion.desc()).all()
    return render_template(
        "proyectos.html",
        proyectos=lista,
        estados=ESTADO_PROYECTO_CHOICES,
        filtro_estado=filtro_estado,
    )


@app.route("/transparencia")
def transparencia():
    rendiciones = RendicionCuenta.query.order_by(RendicionCuenta.fecha_publicacion.desc()).all()
    return render_template("transparencia.html", rendiciones=rendiciones)


@app.route("/directiva")
def directiva():
    miembros = DirectivaMiembro.query.order_by(DirectivaMiembro.orden.asc()).all()
    return render_template("directiva.html", miembros=miembros)


@app.route("/contacto", methods=["GET", "POST"])
def contacto():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        email = request.form.get("email", "").strip()
        telefono = request.form.get("telefono", "").strip()
        mensaje = request.form.get("mensaje", "").strip()

        if not nombre or not email or not mensaje:
            flash("Por favor completa nombre, correo y mensaje.", "error")
            return render_template("contacto.html")

        nuevo = MensajeContacto(
            nombre=nombre, email=email, telefono=telefono, mensaje=mensaje
        )
        db.session.add(nuevo)
        db.session.commit()
        flash("¡Gracias! Tu mensaje fue enviado a la directiva.", "success")
        return redirect(url_for("contacto"))

    return render_template("contacto.html")


ESTADO_CLASS_MAP = {
    "Postulado": "badge-postulado",
    "Aprobado": "badge-aprobado",
    "Rechazado": "badge-rechazado",
    "En ejecución": "badge-en-ejecucion",
    "Finalizado": "badge-finalizado",
}


@app.context_processor
def inject_now():
    return {
        "anio_actual": datetime.now().year,
        "estado_class": lambda estado: ESTADO_CLASS_MAP.get(estado, "badge-postulado"),
    }


def crear_datos_ejemplo():
    """Crea contenido de ejemplo si la base de datos está vacía, para que el sitio no se vea en blanco."""
    if Noticia.query.first() is not None:
        return

    db.session.add_all(
        [
            Noticia(
                titulo="Reunión ordinaria de vecinos",
                resumen="Convocamos a todos los socios y socias a la reunión mensual de la junta.",
                contenido="Se realizará la reunión ordinaria en la sede vecinal. Se tratarán temas de seguridad, "
                "áreas verdes y el estado de los proyectos postulados durante el año.",
                imagen_url="",
            ),
            Noticia(
                titulo="Inicio de trabajos de mejoramiento de la plaza",
                resumen="Comienza la primera etapa del proyecto de mejoramiento del espacio público.",
                contenido="Gracias al proyecto aprobado por el Fondo de Desarrollo Vecinal, esta semana comienzan "
                "los trabajos de mejoramiento de la plaza central del cerro.",
                imagen_url="",
            ),
        ]
    )

    db.session.add_all(
        [
            Proyecto(
                nombre="Mejoramiento de plaza central",
                descripcion="Renovación de juegos infantiles, áreas verdes e iluminación de la plaza principal.",
                estado="En ejecución",
                fuente_financiamiento="Fondo de Desarrollo Vecinal (FONDEVE)",
                monto=8500000,
            ),
            Proyecto(
                nombre="Instalación de cámaras de seguridad",
                descripcion="Postulación para instalar cámaras comunitarias en los accesos principales del cerro.",
                estado="Postulado",
                fuente_financiamiento="Fondo Nacional de Seguridad Pública",
                monto=4200000,
            ),
            Proyecto(
                nombre="Sede social comunitaria",
                descripcion="Ampliación de la sede vecinal para actividades comunitarias.",
                estado="Aprobado",
                fuente_financiamiento="Municipalidad",
                monto=15000000,
            ),
        ]
    )

    rendicion_2025 = RendicionCuenta(
        titulo="Rendición de cuentas anual 2025",
        periodo="2025",
        descripcion="Balance de ingresos y gastos de la junta de vecinos durante el año 2025.",
        archivo_url="",
    )
    rendicion_2025.movimientos = [
        MovimientoRendicion(
            fecha=date(2025, 1, 15),
            concepto="Aporte municipal FONDEVE",
            tipo="Ingreso",
            monto=8500000,
        ),
        MovimientoRendicion(
            fecha=date(2025, 12, 1),
            concepto="Cuotas sociales (enero a diciembre)",
            tipo="Ingreso",
            monto=1200000,
        ),
        MovimientoRendicion(
            fecha=date(2025, 3, 10),
            concepto="Materiales mejoramiento de plaza",
            tipo="Gasto",
            monto=3200000,
        ),
        MovimientoRendicion(
            fecha=date(2025, 6, 5),
            concepto="Pago de electricidad sede social",
            tipo="Gasto",
            monto=450000,
        ),
        MovimientoRendicion(
            fecha=date(2025, 8, 20),
            concepto="Insumos actividad Día del Niño",
            tipo="Gasto",
            monto=280000,
        ),
    ]
    db.session.add(rendicion_2025)

    db.session.add_all(
        [
            DirectivaMiembro(nombre="María Elena Soto Contreras", cargo="Presidente/a", orden=1),
            DirectivaMiembro(nombre="Juan Carlos Pérez Muñoz", cargo="Secretario/a", orden=2),
            DirectivaMiembro(nombre="Rosa Isabel Fuentes Vargas", cargo="Tesorero/a", orden=3),
            DirectivaMiembro(nombre="Pedro Antonio Sánchez Rojas", cargo="Director/a", orden=4),
            DirectivaMiembro(nombre="Carmen Gloria Reyes Castro", cargo="Delegada de Seguridad", orden=5),
        ]
    )

    db.session.commit()


with app.app_context():
    os.makedirs(os.path.join(BASE_DIR, "instance"), exist_ok=True)
    db.create_all()
    crear_datos_ejemplo()


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
