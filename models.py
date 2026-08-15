from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Noticia(db.Model):
    """Noticias y actividades de la junta de vecinos."""
    __tablename__ = "noticias"

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    resumen = db.Column(db.String(400), nullable=False)
    contenido = db.Column(db.Text, nullable=False)
    imagen_url = db.Column(db.String(300))
    fecha_evento = db.Column(db.Date)  # opcional, si es una actividad con fecha
    fecha_publicacion = db.Column(db.DateTime, default=datetime.utcnow)
    publicado = db.Column(db.Boolean, default=True)

    def __repr__(self):
        return f"<Noticia {self.titulo}>"


ESTADO_PROYECTO_CHOICES = ["Postulado", "Aprobado", "Rechazado", "En ejecución", "Finalizado"]


class Proyecto(db.Model):
    """Proyectos postulados/aprobados/rechazados/en ejecución de la junta."""
    __tablename__ = "proyectos"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(200), nullable=False)
    descripcion = db.Column(db.Text, nullable=False)
    estado = db.Column(db.String(30), nullable=False, default="Postulado")
    fuente_financiamiento = db.Column(db.String(200))
    monto = db.Column(db.Integer)  # en pesos chilenos, opcional
    fecha_actualizacion = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Proyecto {self.nombre} ({self.estado})>"


class RendicionCuenta(db.Model):
    """Documentos de transparencia / rendición de cuentas."""
    __tablename__ = "rendiciones"

    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    periodo = db.Column(db.String(50), nullable=False)  # ej: "Marzo 2026"
    descripcion = db.Column(db.String(400))
    archivo_url = db.Column(db.String(300))  # link a PDF u otro documento
    fecha_publicacion = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Rendicion {self.titulo} - {self.periodo}>"


class MensajeContacto(db.Model):
    """Mensajes enviados por vecinos a través del formulario de contacto."""
    __tablename__ = "mensajes_contacto"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    telefono = db.Column(db.String(50))
    mensaje = db.Column(db.Text, nullable=False)
    fecha_envio = db.Column(db.DateTime, default=datetime.utcnow)
    leido = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f"<MensajeContacto de {self.nombre}>"


class DirectivaMiembro(db.Model):
    """Miembros de la directiva de la junta de vecinos."""
    __tablename__ = "directiva"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    cargo = db.Column(db.String(100), nullable=False)
    orden = db.Column(db.Integer, default=0)  # para ordenar en la página

    def __repr__(self):
        return f"<DirectivaMiembro {self.nombre} - {self.cargo}>"
