"""Pruebas del sitio de la Junta de Vecinos N.° 2 · Cerro Esperanza."""

import os
import re
import tempfile
from io import BytesIO
from datetime import timedelta

import pytest
from werkzeug.security import generate_password_hash

import db as base


@pytest.fixture()
def app_prueba(monkeypatch):
    carpeta = tempfile.mkdtemp()
    monkeypatch.setattr(base, "INSTANCE_DIR", carpeta)
    monkeypatch.setattr(base, "DB_PATH", os.path.join(carpeta, "prueba.db"))
    import app as modulo

    modulo.app.config["TESTING"] = True
    modulo.app.config["UPLOAD_FOLDER"] = os.path.join(carpeta, "uploads")
    modulo.app.config["PRIVATE_UPLOAD_FOLDER"] = os.path.join(carpeta, "private")
    base.init_db(modulo.app)
    return modulo.app


@pytest.fixture()
def cliente(app_prueba):
    with app_prueba.test_client() as c:
        yield c


def ingresar(cliente, usuario="admin", clave="esperanza2026"):
    return cliente.post(
        "/login", data={"usuario": usuario, "clave": clave}, follow_redirects=True
    )


def fecha_local():
    import app as modulo
    return modulo.fecha_hoy()


# --- Páginas públicas ------------------------------------------------------
@pytest.mark.parametrize(
    "ruta",
    [
        "/",
        "/noticias",
        "/proyectos",
        "/proyectos?estado=Postulado",
        "/plan-maestro",
        "/transparencia",
        "/documentos",
        "/documentos?categoria=Estatutos",
        "/sede",
        "/sede/reservar",
        "/directorio",
        "/directorio?rubro=Alimentación&q=pan",
        "/directorio/inscribir",
        "/certificado-residencia",
        "/certificado-residencia/estado",
        "/quienes-somos",
        "/contacto",
        "/login",
        "/registro",
    ],
)
def test_paginas_publicas_responden(cliente, ruta):
    assert cliente.get(ruta).status_code == 200


def test_pagina_movilidad_responde(cliente):
    """Verifica que la página pública de movilidad y accesibilidad responda 200 OK y contenga la información clave."""
    respuesta = cliente.get("/movilidad")
    assert respuesta.status_code == 200
    contenido = respuesta.get_data(as_text=True)
    assert "Movilidad y Accesibilidad" in contenido
    assert "Micro 505" in contenido or "505" in contenido
    assert "260" in contenido
    assert "190" in contenido
    assert "mapa-comunitario" in contenido
    assert "leaflet" in contenido.lower()



def test_detalle_de_noticia(cliente):
    assert cliente.get("/noticias/1").status_code == 200
    assert cliente.get("/noticias/9999").status_code == 404


def test_calendario_navega_entre_semanas(cliente):
    otra = (fecha_local() + timedelta(days=7)).isoformat()
    assert cliente.get(f"/sede?semana={otra}").status_code == 200
    assert cliente.get("/sede?vista=mes").status_code == 200


def test_calendario_muestra_solo_intervalos_disponibles(cliente):
    hoy = fecha_local()
    lunes = hoy + timedelta(days=(7 - hoy.weekday()) % 7 or 7)
    respuesta = cliente.get(f"/sede?semana={lunes.isoformat()}&disponibles=1")
    assert respuesta.status_code == 200
    texto = respuesta.get_data(as_text=True)
    assert "Horarios disponibles" in texto
    assert "18:30–21:00" in texto
    assert cliente.get("/sede?vista=mes&mes=2099-02&disponibles=1").status_code == 200


# --- Formularios públicos --------------------------------------------------
def test_reserva_se_registra_como_pendiente(cliente, app_prueba):
    fecha = fecha_local() + timedelta(days=30)
    respuesta = cliente.post(
        "/sede/reservar",
        data={
            "fecha": fecha.isoformat(),
            "hora_inicio": "10:00",
            "hora_fin": "13:00",
            "actividad": "Reunión de prueba",
            "solicitante": "Vecina de prueba",
            "telefono": "+56 9 0000 0000",
            "acepta_aporte": "on",
        },
        follow_redirects=True,
    )
    assert respuesta.status_code == 200
    with app_prueba.app_context():
        fila = base.consultar(
            "SELECT * FROM reservas WHERE actividad = 'Reunión de prueba'", uno=True
        )
        assert fila is not None and fila["estado"] == "Pendiente"


def test_reserva_rechaza_fecha_pasada(cliente, app_prueba):
    ayer = fecha_local() - timedelta(days=1)
    cliente.post(
        "/sede/reservar",
        data={
            "fecha": ayer.isoformat(), "hora_inicio": "10:00", "hora_fin": "11:00",
            "actividad": "Actividad en el pasado", "solicitante": "X", "telefono": "1", "acepta_aporte": "on",
        },
    )
    with app_prueba.app_context():
        assert base.consultar(
            "SELECT * FROM reservas WHERE actividad = 'Actividad en el pasado'", uno=True
        ) is None


def test_reserva_detecta_choque_con_actividad_fija(cliente, app_prueba):
    # El lunes de la próxima semana está tomado de 16:00 a 18:30 por el adulto mayor.
    hoy = fecha_local()
    lunes = hoy + timedelta(days=(7 - hoy.weekday()) % 7 or 7)
    cliente.post(
        "/sede/reservar",
        data={
            "fecha": lunes.isoformat(), "hora_inicio": "17:00", "hora_fin": "19:00",
            "actividad": "Choque con adulto mayor", "solicitante": "X", "telefono": "1", "acepta_aporte": "on",
        },
    )
    with app_prueba.app_context():
        assert base.consultar(
            "SELECT * FROM reservas WHERE actividad = 'Choque con adulto mayor'", uno=True
        ) is None


def test_reserva_rechaza_horas_fuera_del_horario_de_sede(cliente, app_prueba):
    fecha = fecha_local() + timedelta(days=30)
    cliente.post(
        "/sede/reservar",
        data={
            "fecha": fecha.isoformat(), "hora_inicio": "08:00", "hora_fin": "09:00",
            "actividad": "Fuera de horario", "solicitante": "X", "telefono": "1", "acepta_aporte": "on",
        },
    )
    with app_prueba.app_context():
        assert base.consultar(
            "SELECT id FROM reservas WHERE actividad = 'Fuera de horario'", uno=True
        ) is None


def test_solicitud_de_certificado(cliente, app_prueba):
    respuesta = cliente.post(
        "/certificado-residencia",
        data={
            "nombre": "Juan Pérez", "rut": "11.111.111-1",
            "direccion": "Subida Esperanza 123", "telefono": "123", "email": "",
            "motivo": "Trámite municipal",
            "entiendo_revision": "on",
            "documento_domicilio": (BytesIO(b"comprobante de prueba"), "domicilio.pdf"),
        },
        follow_redirects=True,
    )
    assert respuesta.status_code == 200
    with app_prueba.app_context():
        fila = base.consultar(
            "SELECT * FROM certificados WHERE rut = '11.111.111-1'", uno=True
        )
        assert fila is not None and fila["estado"] == "Recibida"
        assert fila["archivo_domicilio"].startswith("privado-cert-")
        nombre = fila["archivo_domicilio"]
    assert cliente.get(f"/archivos/{nombre}").status_code == 404
    ingresar(cliente, "coordinador", "esperanza2026")
    archivo = cliente.get(f"/archivos/{nombre}")
    assert archivo.status_code == 200
    assert "no-store" in archivo.headers["Cache-Control"]
    cliente.get("/logout")

    ingresar(cliente, "admin", "esperanza2026")
    assert cliente.get(f"/archivos/{nombre}").status_code == 200
    cliente.get("/logout")

    with app_prueba.app_context():
        base.ejecutar(
            "INSERT INTO usuarios (nombre, usuario, email, clave_hash, rol, activo, "
            "estado_aprobacion, fecha_creacion) VALUES (?, ?, ?, ?, 'vecino', 1, 'Aprobada', ?)",
            (
                "Vecino de prueba", "vecino_archivo", "vecino_archivo@ejemplo.cl",
                generate_password_hash("clave-vecino-123"), "2026-09-30T12:00:00",
            ),
        )
    ingresar(cliente, "vecino_archivo", "clave-vecino-123")
    assert cliente.get(f"/archivos/{nombre}").status_code == 404


def test_certificado_puede_crear_cuenta_pendiente_y_vincularla(cliente, app_prueba):
    cliente.post(
        "/certificado-residencia",
        data={
            "nombre": "Vecina Certificado", "rut": "12.222.222-2",
            "direccion": "Calle de prueba 12", "telefono": "123",
            "email": "vecina-cert@ejemplo.cl", "crear_cuenta": "on",
            "nuevo_usuario": "vecina_cert", "nueva_clave": "clave-segura-123",
            "confirmar_clave": "clave-segura-123", "entiendo_revision": "on",
            "documento_domicilio": (BytesIO(b"prueba local"), "boleta.png"),
        },
        follow_redirects=True,
    )
    with app_prueba.app_context():
        cuenta = base.consultar(
            "SELECT * FROM usuarios WHERE usuario = 'vecina_cert'", uno=True
        )
        solicitud = base.consultar(
            "SELECT * FROM certificados WHERE rut = '12.222.222-2'", uno=True
        )
        assert cuenta["estado_aprobacion"] == "Pendiente" and not cuenta["activo"]
        assert solicitud["usuario_id"] == cuenta["id"]
    respuesta_login = cliente.post(
        "/login", data={"usuario": "vecina_cert", "clave": "clave-segura-123"},
        follow_redirects=True,
    )
    assert "pendiente de validación" in respuesta_login.get_data(as_text=True).lower() or "pendiente de aprobación" in respuesta_login.get_data(as_text=True).lower()
    ingresar(cliente)
    cliente.post(f"/panel/usuarios/{cuenta['id']}/aprobar")
    cliente.get("/logout")
    assert ingresar(cliente, "vecina_cert", "clave-segura-123").status_code == 200
    assert cliente.get("/mis-solicitudes").status_code == 200


def test_inscripcion_de_servicio_queda_sin_aprobar(cliente, app_prueba):
    # Sin sesión de socio, no permite inscribir servicio (muestra pantalla de restricción)
    resp_anon = cliente.post(
        "/directorio/inscribir",
        data={
            "nombre_servicio": "Costurería Ana", "vecino": "Ana", "rubro": "Otros",
            "telefono": "+56912345678", "whatsapp": "+56999999999",
        },
        follow_redirects=True,
    )
    assert resp_anon.status_code in (200, 403)
    assert "exclusivo para socios" in resp_anon.get_data(as_text=True).lower()

    # Con sesión de socio acreditado
    with app_prueba.app_context():
        base.ejecutar(
            "INSERT INTO usuarios (nombre, usuario, email, clave_hash, rol, activo, estado_aprobacion, fecha_creacion) "
            "VALUES ('Ana Socia', 'ana_socia', 'ana@ejemplo.cl', ?, 'socio', 1, 'Aprobada', '2026-10-01T10:00:00')",
            (generate_password_hash("clave-socia-123"),)
        )
    ingresar(cliente, "ana_socia", "clave-socia-123")
    cliente.post(
        "/directorio/inscribir",
        data={
            "nombre_servicio": "Costurería Ana", "vecino": "Ana", "rubro": "Otros",
            "telefono": "+56912345678", "whatsapp": "+56999999999",
        },
        follow_redirects=True,
    )
    with app_prueba.app_context():
        fila = base.consultar(
            "SELECT * FROM servicios WHERE nombre_servicio = 'Costurería Ana'", uno=True
        )
        assert fila is not None and fila["aprobado"] == 0
    # No aparece en el directorio público hasta que la directiva lo apruebe
    assert "Costurería Ana" not in cliente.get("/directorio").get_data(as_text=True)


def test_coordinador_aprueba_solicitud_del_directorio(cliente, app_prueba):
    with app_prueba.app_context():
        base.ejecutar(
            "INSERT INTO usuarios (nombre, usuario, email, clave_hash, rol, activo, estado_aprobacion, fecha_creacion) "
            "VALUES ('Socio Pan', 'socio_pan', 'pan@ejemplo.cl', ?, 'socio', 1, 'Aprobada', '2026-10-01T10:00:00')",
            (generate_password_hash("clave-socio-123"),)
        )
    ingresar(cliente, "socio_pan", "clave-socio-123")
    cliente.post(
        "/directorio/inscribir",
        data={"nombre_servicio": "Pan de prueba", "vecino": "Vecino", "rubro": "Otros",
              "telefono": "+56912345678"},
        follow_redirects=True,
    )
    cliente.get("/logout")
    with app_prueba.app_context():
        fila = base.consultar(
            "SELECT id FROM servicios WHERE nombre_servicio = 'Pan de prueba'", uno=True
        )
    ingresar(cliente, "coordinador", "esperanza2026")
    assert cliente.get("/panel/directorio-pendiente").status_code == 200
    cliente.post(f"/panel/directorio/{fila['id']}/aprobar")
    assert "Pan de prueba" in cliente.get("/directorio").get_data(as_text=True)


def test_coordinador_no_puede_publicar_servicios_sin_aprobar(cliente, app_prueba):
    ingresar(cliente, "coordinador", "esperanza2026")
    formulario = cliente.get("/panel/servicios/nuevo").get_data(as_text=True)
    assert 'name="aprobado"' not in formulario
    cliente.post(
        "/panel/servicios/nuevo",
        data={"nombre_servicio": "Servicio pendiente", "vecino": "Vecino",
              "rubro": "Otros", "telefono": "123", "aprobado": "on"},
    )
    with app_prueba.app_context():
        fila = base.consultar(
            "SELECT * FROM servicios WHERE nombre_servicio = 'Servicio pendiente'", uno=True
        )
        assert fila["aprobado"] == 0
    assert "Servicio pendiente" not in cliente.get("/directorio").get_data(as_text=True)


def test_mensaje_de_contacto(cliente, app_prueba):
    cliente.post(
        "/contacto",
        data={"nombre": "Vecino", "email": "v@ejemplo.cl", "telefono": "",
              "mensaje": "Consulta de prueba"},
        follow_redirects=True,
    )
    with app_prueba.app_context():
        assert base.consultar(
            "SELECT * FROM mensajes WHERE mensaje = 'Consulta de prueba'", uno=True
        ) is not None


# --- Sesión y permisos -----------------------------------------------------
def test_panel_exige_iniciar_sesion(cliente):
    respuesta = cliente.get("/panel", follow_redirects=False)
    assert respuesta.status_code == 302
    assert "/login" in respuesta.headers["Location"]


def test_administrador_entra_al_panel(cliente):
    assert ingresar(cliente).status_code == 200
    for ruta in ("/panel", "/panel/reservas", "/panel/certificados", "/panel/mensajes",
                 "/panel/noticias", "/panel/proyectos", "/panel/documentos",
                 "/panel/bloques", "/panel/servicios", "/panel/directiva",
                 "/panel/rendiciones", "/panel/eventos", "/panel/usuarios",
                 "/panel/contenido", "/panel/noticias/nuevo",
                 "/panel/rendiciones/1/movimientos"):
        assert cliente.get(ruta).status_code == 200, ruta


def test_coordinador_no_ve_usuarios_ni_contenido(cliente):
    ingresar(cliente, "coordinador", "esperanza2026")
    assert cliente.get("/panel/reservas").status_code == 200
    assert cliente.get("/panel/usuarios", follow_redirects=False).status_code == 302
    assert cliente.get("/panel/contenido", follow_redirects=False).status_code == 302
    assert cliente.get("/panel/documentos").status_code == 200
    assert cliente.get("/panel/permisos-coordinador", follow_redirects=False).status_code == 302
    assert cliente.get("/panel/rendiciones", follow_redirects=False).status_code == 302


def test_vecino_se_registra_y_ve_sus_solicitudes(cliente):
    respuesta = cliente.post(
        "/registro",
        data={"nombre": "Vecina Nueva", "rut": "15.555.555-6", "usuario": "vecina", "email": "vecina@ejemplo.cl",
              "clave": "clave123", "confirmar_clave": "clave123"},
        follow_redirects=True,
    )
    assert respuesta.status_code == 200
    assert cliente.get("/mis-solicitudes", follow_redirects=False).status_code == 302
    cliente_admin = cliente.application.test_client()
    ingresar(cliente_admin)
    with cliente.application.app_context():
        cuenta = base.consultar("SELECT id FROM usuarios WHERE usuario = 'vecina'", uno=True)
    cliente_admin.post(f"/panel/usuarios/{cuenta['id']}/aprobar")
    assert ingresar(cliente, "vecina", "clave123").status_code == 200
    assert cliente.get("/mis-solicitudes").status_code == 200
    # Un vecino no puede administrar
    assert cliente.get("/panel/noticias", follow_redirects=False).status_code == 302


def test_admin_puede_crear_vecino_aprobado(cliente, app_prueba):
    ingresar(cliente)
    respuesta = cliente.post(
        "/panel/crear-vecino",
        data={"nombre": "Vecino creado", "usuario": "vecino_admin", "email": "",
              "clave": "clave-segura", "confirmar_clave": "clave-segura"},
        follow_redirects=True,
    )
    assert "quedó activa" in respuesta.get_data(as_text=True)
    with app_prueba.app_context():
        fila = base.consultar(
            "SELECT * FROM usuarios WHERE usuario = 'vecino_admin'", uno=True
        )
        assert fila["estado_aprobacion"] == "Aprobada" and fila["activo"]


def test_admin_controla_si_coordinador_puede_crear_vecinos(cliente, app_prueba):
    ingresar(cliente)
    with app_prueba.app_context():
        coord = base.consultar("SELECT id FROM usuarios WHERE rol = 'coordinador'", uno=True)
    # Admin asigna permiso modular permiso_socios al coordinador
    cliente.post("/panel/permisos-coordinador", data={"usuario_id": coord["id"], "permiso_socios": "on"})
    cliente.get("/logout")
    ingresar(cliente, "coordinador", "esperanza2026")
    assert cliente.get("/panel/crear-socio").status_code == 200
    assert cliente.get("/panel/crear-vecino").status_code == 200
    cliente.post(
        "/panel/crear-socio",
        data={"nombre": "Vecino coordinado", "usuario": "vecino_coord", "email": "",
              "clave": "clave-segura", "confirmar_clave": "clave-segura"},
    )
    with app_prueba.app_context():
        fila = base.consultar(
            "SELECT * FROM usuarios WHERE usuario = 'vecino_coord'", uno=True
        )
        assert fila["estado_aprobacion"] == "Pendiente" and not fila["activo"]
    # Pero con permiso_socios ahora sí puede ver usuarios pendientes
    assert cliente.get("/panel/usuarios-pendientes").status_code == 200


def test_clave_incorrecta_no_inicia_sesion(cliente):
    ingresar(cliente, "admin", "mala")
    assert cliente.get("/panel", follow_redirects=False).status_code == 302


# --- Panel: flujo completo -------------------------------------------------
def test_aprobar_una_reserva(cliente, app_prueba):
    ingresar(cliente)
    with app_prueba.app_context():
        pendiente = base.consultar(
            "SELECT * FROM reservas WHERE estado = 'Pendiente'", uno=True
        )
    assert pendiente is not None
    cliente.post(
        f"/panel/reservas/{pendiente['id']}/estado",
        data={"estado": "Aprobada", "observacion": "Confirmada por teléfono"},
        follow_redirects=True,
    )
    with app_prueba.app_context():
        fila = base.consultar(
            "SELECT * FROM reservas WHERE id = ?", (pendiente["id"],), uno=True
        )
        assert fila["estado"] == "Aprobada"
        assert fila["revisada_por"] != ""


def test_crear_editar_y_eliminar_noticia(cliente, app_prueba):
    ingresar(cliente)
    cliente.post(
        "/panel/noticias/nuevo",
        data={"titulo": "Noticia de prueba", "resumen": "Resumen", "contenido": "Contenido",
              "fecha_evento": "", "imagen_url": "", "publicado": "on"},
        follow_redirects=True,
    )
    with app_prueba.app_context():
        fila = base.consultar(
            "SELECT * FROM noticias WHERE titulo = 'Noticia de prueba'", uno=True
        )
    assert fila is not None
    cliente.post(
        f"/panel/noticias/{fila['id']}/editar",
        data={"titulo": "Noticia editada", "resumen": "Resumen", "contenido": "Contenido",
              "fecha_evento": "", "imagen_url": "", "publicado": "on"},
        follow_redirects=True,
    )
    with app_prueba.app_context():
        assert base.consultar(
            "SELECT * FROM noticias WHERE id = ?", (fila["id"],), uno=True
        )["titulo"] == "Noticia editada"
    cliente.post(f"/panel/noticias/{fila['id']}/eliminar", follow_redirects=True)
    with app_prueba.app_context():
        assert base.consultar(
            "SELECT * FROM noticias WHERE id = ?", (fila["id"],), uno=True
        ) is None


def test_agregar_movimiento_a_una_actividad(cliente, app_prueba):
    ingresar(cliente)
    cliente.post(
        "/panel/eventos/1/movimientos",
        data={"fecha": fecha_local().isoformat(), "concepto": "Aporte de prueba",
              "tipo": "Ingreso", "monto": "50000"},
        follow_redirects=True,
    )
    with app_prueba.app_context():
        assert base.consultar(
            "SELECT * FROM movimientos WHERE concepto = 'Aporte de prueba'", uno=True
        ) is not None
    assert "Aporte de prueba" in cliente.get("/transparencia").get_data(as_text=True)
    cliente.get("/logout")
    anonimo = cliente.get("/transparencia").get_data(as_text=True)
    assert "Aporte de prueba" not in anonimo
    assert "50.000" not in anonimo


def test_textos_del_sitio_se_actualizan(cliente, app_prueba):
    ingresar(cliente)
    cliente.post(
        "/panel/contenido",
        data={"socios_inscritos": "2450", "mision": "Nueva misión", "vision": "",
              "historia": "", "direccion_sede": "", "horario_atencion": "",
              "horario_sede_inicio": "08:30", "horario_sede_fin": "20:00",
              "email_contacto": "", "telefono_contacto": "", "whatsapp_grupo": "",
              "facebook": "", "instagram": "", "plan_maestro_intro": "",
              "requisitos_certificado": "Cédula de identidad",
              "aviso_privacidad": "", "aviso_uso": ""},
        follow_redirects=True,
    )
    assert "2450" in cliente.get("/").get_data(as_text=True)
    assert "Cédula de identidad" in cliente.get("/certificado-residencia").get_data(as_text=True)


def test_coordinador_publica_noticias_y_documentos_con_adjuntos(cliente, app_prueba):
    ingresar(cliente, "coordinador", "esperanza2026")
    cliente.post(
        "/panel/noticias/nuevo",
        data={"titulo": "Aviso de prueba", "resumen": "Resumen de prueba",
              "contenido": "Información útil", "fecha_evento": "", "imagen_url": "",
              "publicado": "on", "imagen_archivo":
                  (BytesIO(b"imagen ficticia local"), "noticia.png")},
        follow_redirects=True,
    )
    cliente.post(
        "/panel/documentos/nuevo",
        data={"titulo": "Acta de prueba", "categoria": "Actas de asamblea",
              "descripcion": "Acta de la reunión de prueba", "archivo":
                  (BytesIO(b"documento ficticio local"), "reunion.pdf"), "enlace": ""},
        follow_redirects=True,
    )
    with app_prueba.app_context():
        noticia = base.consultar(
            "SELECT * FROM noticias WHERE titulo = 'Aviso de prueba'", uno=True
        )
        doc = base.consultar(
            "SELECT * FROM documentos WHERE titulo = 'Acta de prueba'", uno=True
        )
        assert noticia["imagen_url"].startswith("/static/uploads/")
        assert doc["archivo"] and not doc["archivo"].startswith("privado-")
    assert "Acta de prueba" in cliente.get("/documentos").get_data(as_text=True)
    formulario = cliente.get("/panel/documentos/nuevo").get_data(as_text=True)
    assert 'value="Rendiciones de cuentas"' not in formulario
    denegado = cliente.post(
        "/panel/documentos/nuevo",
        data={"titulo": "Respaldo no autorizado", "categoria": "Rendiciones de cuentas",
              "descripcion": "", "enlace": ""},
    )
    assert denegado.status_code == 403


def test_montos_y_documentos_financieros_solo_se_muestran_con_cuenta(cliente, app_prueba):
    ingresar(cliente)
    cliente.post(
        "/panel/eventos/1/movimientos",
        data={"fecha": fecha_local().isoformat(), "concepto": "Detalle solo vecino",
              "tipo": "Gasto", "monto": "987654"},
    )
    cliente.post(
        "/panel/documentos/nuevo",
        data={"titulo": "Respaldo solo vecino", "categoria": "Rendiciones de cuentas",
              "descripcion": "Documento de finanzas", "archivo":
                  (BytesIO(b"respaldo financiero ficticio"), "respaldo.pdf"), "enlace": ""},
    )
    with app_prueba.app_context():
        doc = base.consultar(
            "SELECT * FROM documentos WHERE titulo = 'Respaldo solo vecino'", uno=True
        )
        base.ejecutar(
            "UPDATE proyectos SET monto = 987654, fuente_financiamiento = 'Fondo ficticio' "
            "WHERE id = 1"
        )
        assert doc["archivo"].startswith("privado-")
    url_archivo = f"/archivos/{doc['archivo']}"
    anonimo = app_prueba.test_client()
    texto_publico = anonimo.get("/transparencia").get_data(as_text=True)
    assert "Detalle solo vecino" not in texto_publico
    assert "987.654" not in texto_publico
    assert "Respaldo solo vecino" not in texto_publico
    assert anonimo.get(url_archivo).status_code == 404
    assert "987.654" not in anonimo.get("/proyectos").get_data(as_text=True)
    assert "Fondo ficticio" not in anonimo.get("/plan-maestro").get_data(as_text=True)

    vecino = app_prueba.test_client()
    ingresar(vecino)
    vecino.post(
        "/panel/crear-vecino",
        data={"nombre": "Vecino lector", "usuario": "vecino_lector", "email": "lector@ejemplo.cl",
              "clave": "clave-segura", "confirmar_clave": "clave-segura"},
    )
    vecino.get("/logout")
    ingresar(vecino, "vecino_lector", "clave-segura")
    texto_con_cuenta = vecino.get("/transparencia").get_data(as_text=True)
    assert "Detalle solo vecino" in texto_con_cuenta
    assert "987.654" in texto_con_cuenta
    assert "Respaldo solo vecino" in texto_con_cuenta
    assert vecino.get(url_archivo).status_code == 200
    assert "987.654" in vecino.get("/proyectos").get_data(as_text=True)


def test_pagina_inexistente_muestra_error(cliente):
    assert cliente.get("/esta-pagina-no-existe").status_code == 404


def test_api_disponibilidad_sede(cliente):
    res = cliente.get("/api/sede/disponibilidad")
    assert res.status_code == 200
    datos = res.get_json()
    assert "semanas" in datos
    assert "mes_nombre" in datos
    assert len(datos["semanas"]) > 0
    # Comprobar que los días tienen slots
    primer_dia = datos["semanas"][0][0]
    assert "slots" in primer_dia
    assert "disponible" in primer_dia


def test_admin_puede_editar_y_eliminar_reserva(cliente, app_prueba):
    ingresar(cliente)
    with app_prueba.app_context():
        reserva = base.consultar("SELECT * FROM reservas LIMIT 1", uno=True)
    assert reserva is not None

    fecha_nueva = (fecha_local() + timedelta(days=25)).isoformat()
    resp = cliente.post(
        f"/panel/reservas/{reserva['id']}/editar",
        data={
            "fecha": fecha_nueva,
            "hora_inicio": "10:00",
            "hora_fin": "12:00",
            "actividad": "Actividad modificada",
            "solicitante": "Vecina Editada",
            "telefono": "+56 9 8888 7777",
            "email": "editada@ejemplo.cl",
            "personas": "20",
            "estado": "Aprobada",
            "observacion": "Ajuste de horario por panel",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app_prueba.app_context():
        actualizada = base.consultar("SELECT * FROM reservas WHERE id = ?", (reserva["id"],), uno=True)
        assert actualizada["actividad"] == "Actividad modificada"
        assert actualizada["solicitante"] == "Vecina Editada"
        assert actualizada["fecha"] == fecha_nueva
        assert actualizada["estado"] == "Aprobada"

    # Eliminar la reserva
    resp_del = cliente.post(f"/panel/reservas/{reserva['id']}/eliminar", follow_redirects=True)
    assert resp_del.status_code == 200
    with app_prueba.app_context():
        assert base.consultar("SELECT * FROM reservas WHERE id = ?", (reserva["id"],), uno=True) is None


def test_imagenes_en_portada_y_noticias(cliente):
    # Portada
    portada = cliente.get("/").get_data(as_text=True)
    assert "hero_comunidad.jpg" in portada
    assert "noticia_asamblea.jpg" in portada

    # Noticias
    noticias = cliente.get("/noticias").get_data(as_text=True)
    assert "noticia_asamblea.jpg" in noticias

    # Proyectos
    proyectos = cliente.get("/proyectos").get_data(as_text=True)
    assert "noticia_plaza.jpg" in proyectos


def test_sede_muestra_fotografia(cliente):
    resp = cliente.get("/sede").get_data(as_text=True)
    assert "sede_comunitaria.jpg" in resp
    assert "sede-hero-foto" in resp


def test_directorio_muestra_foto_y_placeholder(cliente):
    resp = cliente.get("/directorio").get_data(as_text=True)
    assert "directorio-card" in resp
    # Hay al menos un servicio con foto y servicios con placeholder
    assert "directorio-avatar-img" in resp or "directorio-avatar-placeholder" in resp


def test_quienes_somos_directiva_fotos(cliente):
    resp = cliente.get("/quienes-somos").get_data(as_text=True)
    assert "directiva-card" in resp
    assert "directiva-foto-img" in resp
    assert "directiva_presidenta.jpg" in resp


def test_panel_gestion_imagenes_removido(cliente):
    ingresar(cliente, "admin", "esperanza2026")
    resp = cliente.get("/panel/imagenes")
    assert resp.status_code == 404
    # Verificar que el menú no incluya la opción de fotografías e imágenes
    panel_home = cliente.get("/panel").get_data(as_text=True)
    assert "Fotografías e imágenes" not in panel_home


def test_panel_filtros_servicios(cliente):
    ingresar(cliente, "admin", "esperanza2026")
    # Vista completa
    resp = cliente.get("/panel/servicios")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Buscar por nombre o palabra clave" in html
    assert "Filtrar por rubro" in html

    # Búsqueda por rubro
    resp_rubro = cliente.get("/panel/servicios?rubro=Alimentación")
    assert resp_rubro.status_code == 200

    # Búsqueda combinada
    resp_comb = cliente.get("/panel/servicios?q=pan&rubro=Alimentación")
    assert resp_comb.status_code == 200


def test_panel_filtros_proyectos(cliente):
    ingresar(cliente, "admin", "esperanza2026")
    resp = cliente.get("/panel/proyectos")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Filtrar por estado" in html

    # Filtrar por estado
    resp_estado = cliente.get("/panel/proyectos?estado=En ejecución")
    assert resp_estado.status_code == 200


def test_panel_filtros_rendiciones(cliente):
    ingresar(cliente, "admin", "esperanza2026")
    resp = cliente.get("/panel/rendiciones")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Filtrar por período" in html
    assert "Buscar por concepto o actividad" in html


def test_panel_filtros_eventos(cliente):
    ingresar(cliente, "admin", "esperanza2026")
    resp = cliente.get("/panel/eventos")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Ordenar por fecha" in html
    assert "Filtrar por año" in html
    # Orden más antiguos
    resp_ant = cliente.get("/panel/eventos?orden=antiguos")
    assert resp_ant.status_code == 200





# ===========================================================================
# Pruebas de Nuevos Requerimientos y Refactorización Integral
# ===========================================================================

def test_comision_revisora_informe_inmutable(cliente, app_prueba):
    # Crear usuario de Comisión Revisora
    with app_prueba.app_context():
        base.ejecutar(
            "INSERT INTO usuarios (nombre, usuario, email, clave_hash, rol, activo, estado_aprobacion, fecha_creacion) "
            "VALUES ('Auditor Comisión', 'auditor_rev', 'auditor@ejemplo.cl', ?, 'comision_revisora', 1, 'Aprobada', '2026-10-01T10:00:00')",
            (generate_password_hash("clave-auditor-123"),)
        )
    # Login como auditor
    ingresar(cliente, "auditor_rev", "clave-auditor-123")
    assert cliente.get("/panel/auditorias").status_code == 200
    assert cliente.get("/panel/auditorias/nuevo").status_code == 200

    # Subir informe de auditoría
    resp_subida = cliente.post(
        "/panel/auditorias/nuevo",
        data={
            "titulo": "Balance Anual Auditado 2025",
            "tipo": "Balance auditado",
            "descripcion": "Revisión conforme a la Ley 19.418 sin objeciones.",
            "archivo": (BytesIO(b"contenido-balance-pdf"), "balance2025.pdf"),
        },
        follow_redirects=True,
    )
    assert resp_subida.status_code == 200

    with app_prueba.app_context():
        inf = base.consultar("SELECT * FROM informes_auditoria WHERE titulo = 'Balance Anual Auditado 2025'", uno=True)
        assert inf is not None
        assert inf["tipo"] == "Balance auditado"

    # Verificar inmutabilidad: editar o eliminar debe responder con 403
    assert cliente.post(f"/panel/auditorias/{inf['id']}/editar").status_code == 403
    assert cliente.post(f"/panel/auditorias/{inf['id']}/eliminar").status_code == 403

    # Incluso el Administrador no puede editar ni eliminar el informe
    cliente.get("/logout")
    ingresar(cliente, "admin", "esperanza2026")
    assert cliente.post(f"/panel/auditorias/{inf['id']}/editar").status_code == 403
    assert cliente.post(f"/panel/auditorias/{inf['id']}/eliminar").status_code == 403


def test_visor_documentos_estatutos_protegidos(cliente, app_prueba):
    with app_prueba.app_context():
        # Crear documento de Estatutos con archivo
        doc_id = base.ejecutar(
            "INSERT INTO documentos (titulo, categoria, descripcion, archivo, fecha_publicacion) "
            "VALUES ('Estatutos Oficiales 2026', 'Estatutos', 'Estatuto interno de la Junta', 'estatuto.pdf', '2026-10-01T10:00:00')"
        )
    # En listado general /documentos
    html = cliente.get("/documentos").get_data(as_text=True)
    assert "Estatutos Oficiales 2026" in html
    assert f"/documentos/{doc_id}/ver" in html

    # Visor en pantalla responde 200 y contiene elemento visor protegido
    resp_visor = cliente.get(f"/documentos/{doc_id}/ver")
    assert resp_visor.status_code == 200
    assert "data-visor-protegido" in resp_visor.get_data(as_text=True)

    # Descarga directa por /archivos/estatuto.pdf debe estar bloqueada para estatutos
    assert cliente.get("/archivos/estatuto.pdf").status_code == 404


def test_carga_masiva_padron_y_cumpleanos(cliente, app_prueba):
    ingresar(cliente, "admin", "esperanza2026")
    csv_datos = (
        "numero_socio,rut,nombre,apellidos,direccion,telefono,email,fecha_nacimiento,fallecido\n"
        "101,11.111.111-1,Juan,Perez Gonzalez,Cerro 123,+56911112222,juan@perez.cl,1980-10-06,0\n"
        "102,12.222.222-2,Maria,Lopez Silva,Cerro 456,+56933334444,maria@lopez.cl,1990-05-15,0\n"
    ).encode("utf-8")

    resp_carga = cliente.post(
        "/admin/cargar-padron",
        data={"archivo": (BytesIO(csv_datos), "padron.csv")},
        follow_redirects=True,
    )
    assert resp_carga.status_code == 200
    assert "2" in resp_carga.get_data(as_text=True)

    with app_prueba.app_context():
        juan = base.consultar("SELECT * FROM usuarios WHERE rut = '11111111-1'", uno=True)
        assert juan is not None
        assert juan["numero_socio"] == "101"
        assert juan["debe_cambiar_clave"] == 1
        # El usuario se genera a partir del RUT sin guion
        assert juan["usuario"] == "111111111"

    # Verificar módulo de socios y cumpleaños
    resp_socios = cliente.get("/panel/socios")
    assert resp_socios.status_code == 200
    assert "Juan" in resp_socios.get_data(as_text=True)
    assert "Padrón de Socios" in resp_socios.get_data(as_text=True)


def test_censo_infantil_flujo_socio_y_exportacion(cliente, app_prueba):
    # Crear y loguear a un socio
    with app_prueba.app_context():
        socio_id = base.ejecutar(
            "INSERT INTO usuarios (nombre, apellidos, usuario, email, clave_hash, rol, activo, estado_aprobacion, fecha_creacion, rut, numero_socio) "
            "VALUES ('Carlos', 'Gomez', 'carlos_socio', 'carlos@ejemplo.cl', ?, 'socio', 1, 'Aprobada', '2026-10-01T10:00:00', '14.444.444-4', '555')",
            (generate_password_hash("clave-carlos-123"),)
        )
    ingresar(cliente, "carlos_socio", "clave-carlos-123")
    
    # Socio inscribe a un menor de 5 años
    resp_insc = cliente.post(
        "/mis-menores",
        data={
            "nombre": "Carlitos Gomez",
            "rut": "26.123.456-4",
            "fecha_nacimiento": "2021-06-15",
        },
        follow_redirects=True,
    )
    assert resp_insc.status_code == 200
    assert "Carlitos Gomez" in resp_insc.get_data(as_text=True)

    # Administrador revisa el censo y exporta la planilla CSV
    cliente.get("/logout")
    ingresar(cliente, "admin", "esperanza2026")
    resp_panel_censo = cliente.get("/panel/censo-infantil")
    assert resp_panel_censo.status_code == 200
    assert "Carlitos Gomez" in resp_panel_censo.get_data(as_text=True)

    # Exportación CSV
    resp_csv = cliente.get("/panel/censo-infantil/exportar")
    assert resp_csv.status_code == 200
    assert "text/csv" in resp_csv.headers["Content-Type"]
    csv_text = resp_csv.get_data(as_text=True)
    assert "Carlitos Gomez" in csv_text
    assert "Carlos Gomez" in csv_text


def test_historia_y_como_asociarse(cliente):
    # Ruta de historia
    resp_hist = cliente.get("/historia")
    assert resp_hist.status_code == 200
    assert "Historia y Memoria del Cerro" in resp_hist.get_data(as_text=True)

    # Ruta de cómo asociarse
    resp_asoc = cliente.get("/como-asociarse")
    assert resp_asoc.status_code == 200
    assert "Cómo asociarse" in resp_asoc.get_data(as_text=True)

    # Quiénes somos enlaza a historia
    resp_qs = cliente.get("/quienes-somos")
    assert resp_qs.status_code == 200
    assert "/historia" in resp_qs.get_data(as_text=True)


def test_inscribir_servicio_bloqueo_no_socio_y_cargas_infantiles(cliente, app_prueba):
    # Usuario anónimo entra a /inscribir-servicio
    resp = cliente.get("/inscribir-servicio")
    assert resp.status_code == 200
    texto = resp.get_data(as_text=True)
    assert "Para inscribir y promocionar tu emprendimiento en la plataforma comunitaria" in texto
    assert "Iniciar Sesión de Socio" in texto
    assert "Información para Asociarse" in texto

    # Intento de POST anónimo es bloqueado con 403
    resp_post = cliente.post("/inscribir-servicio", data={"nombre_servicio": "Test"})
    assert resp_post.status_code == 403

    # Verificación de la vista cargas_infantiles
    with app_prueba.app_context():
        cargas = base.consultar("SELECT * FROM cargas_infantiles")
        assert isinstance(cargas, list)


def test_panel_cargar_padron_alias(cliente):
    ingresar(cliente, "admin", "esperanza2026")
    resp = cliente.get("/panel/cargar-padron")
    assert resp.status_code == 200
    assert "Carga Masiva del Padrón Oficial" in resp.get_data(as_text=True)



# --- Asistente del inicio --------------------------------------------------
@pytest.mark.parametrize(
    "pregunta, esperado",
    [
        ("necesito un papel que diga que vivo aquí", "certificado"),
        ("sertificado de residensia", "certificado"),
        ("quiero arrendar la sede para un cumpleaños", "reservar"),
        ("¿En qué se gastó la plata?", "transparencia"),
        ("necesito un gásfiter", "rubro:Construcción y oficios"),
        ("a qué hora pasa la micro", "movilidad"),
        ("plan maestro", "plan_maestro"),
        ("juguetes de navidad para mis hijos", "mis_menores"),
        ("quién es la presidenta", "quienes_somos"),
        ("ingresar a mi cuenta", "login"),
    ],
)
def test_asistente_entiende_preguntas_comunes(pregunta, esperado):
    import asistente

    resultado = asistente.sugerir(pregunta)
    assert resultado["tipo"] == "resultado"
    assert resultado["destinos"][0]["id"] == esperado


def test_asistente_casos_especiales():
    import asistente

    assert asistente.sugerir("")["tipo"] == "vacio"
    assert asistente.sugerir("hola")["tipo"] == "saludo"
    assert asistente.sugerir("muchas gracias")["tipo"] == "gracias"
    assert asistente.sugerir("asdfgh qwerty")["tipo"] == "sin_resultado"
    emergencia = asistente.sugerir("me robaron la cartera")
    assert emergencia["tipo"] == "emergencia" and "133" in emergencia["mensaje"]


def test_asistente_todos_los_destinos_existen(app_prueba):
    import app as modulo
    import asistente

    with app_prueba.test_request_context():
        for destino in asistente.DESTINOS:
            modulo.url_for(destino["endpoint"], **destino.get("args", {}))


def test_api_asistente_devuelve_enlaces(cliente):
    datos = cliente.get("/api/asistente?q=quiero reservar la sede").get_json()
    assert datos["tipo"] == "resultado"
    assert datos["destinos"][0]["url"] == "/sede/reservar"
    rubro = cliente.get("/api/asistente?q=busco un gasfiter").get_json()
    assert "rubro=Construcci" in rubro["destinos"][0]["url"]


def test_inicio_responde_al_asistente_sin_javascript(cliente):
    texto = cliente.get("/?q=certificado de residencia").get_data(as_text=True)
    assert "asistente-respuesta" in texto
    assert 'href="/certificado-residencia"' in texto
    escapado = cliente.get("/?q=<script>alert(1)</script>").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in escapado


# --- Versión de entrega: padrón real, perfil, producción y pedidos de la contraparte ---
import zipfile
from datetime import date
from xml.sax.saxutils import escape


def xlsx_de_prueba(hojas, filas_vacias=0):
    """Libro .xlsx mínimo (sin librerías) con varias hojas ``[(nombre, filas)]``."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        hojas_xml = "".join(
            f'<sheet name="{n}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, (n, _) in enumerate(hojas)
        )
        z.writestr("xl/workbook.xml", (
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{hojas_xml}</sheets></workbook>"))
        rels = "".join(
            f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/'
            f'2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
            for i in range(len(hojas))
        )
        z.writestr("xl/_rels/workbook.xml.rels",
                   f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        for i, (_, filas) in enumerate(hojas):
            xml_filas = []
            for r, fila in enumerate(filas, start=1):
                celdas = []
                for c, valor in enumerate(fila):
                    ref = f"{chr(65 + c)}{r}"
                    if isinstance(valor, (int, float)):
                        celdas.append(f'<c r="{ref}"><v>{valor}</v></c>')
                    elif valor:
                        celdas.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(valor)}</t></is></c>')
                xml_filas.append(f'<row r="{r}">{"".join(celdas)}</row>')
            for r in range(len(filas) + 1, len(filas) + 1 + filas_vacias):
                xml_filas.append(f'<row r="{r}"/>')
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<sheetData>{''.join(xml_filas)}</sheetData></worksheet>"))
    return buf.getvalue()


def crear_socio(app_prueba, usuario="socia_prueba", email="socia@ejemplo.cl", **extra):
    columnas = {
        "nombre": "Socia", "apellidos": "Prueba", "usuario": usuario, "email": email,
        "clave_hash": generate_password_hash("clave-socia-123"), "rol": "socio", "activo": 1,
        "estado_aprobacion": "Aprobada", "fecha_creacion": "2026-10-01T10:00:00",
    }
    columnas.update(extra)
    with app_prueba.app_context():
        return base.ejecutar(
            f"INSERT INTO usuarios ({', '.join(columnas)}) VALUES ({', '.join('?' * len(columnas))})",
            tuple(columnas.values()),
        )


def test_padron_lee_formato_oficial_de_la_junta(cliente, app_prueba):
    import padron

    resumen = ("total", [["", "Socios"], ["Nombre", "Antes", "Después"], ["Tolson", 10, 24]])
    oficial = ("ordenado por CORRELATIVO", [
        ["N°", "Nombres", "Edad", "E. Civil", "Domicilio", "Carnet (RUT)", "F.Ingreso", "correo", "telefono"],
        [1, "Ana María Rojas Soto", 70, "Casada", "Tolson 12", "11.111.111-1", 2019, "", "+569 11112222"],
        [2, "Luis Pérez Díaz", 55, "Soltero", "Aspillaga 3", "12.222.222-5", 43831, "luis@ejemplo.cl", ""],
        [3, "Rut Malo", 40, "Soltero", "Durazno 1", "5.102.500-0", "2015 / 03-04", "", ""],
    ])
    contenido = xlsx_de_prueba([resumen, oficial], filas_vacias=2500)
    registros = padron.leer_padron(contenido, "resultado de padron.xlsx")
    assert [r["numero_socio"] for r in registros] == ["1", "2", "3"]
    assert "estado_civil" not in registros[0] and "e_civil" not in registros[0]

    ingresar(cliente)
    respuesta = cliente.post(
        "/panel/cargar-padron",
        data={"archivo": (BytesIO(contenido), "resultado de padron.xlsx")},
        follow_redirects=True,
    )
    assert respuesta.status_code == 200
    with app_prueba.app_context():
        ana = base.consultar("SELECT * FROM usuarios WHERE rut = '11111111-1'", uno=True)
        luis = base.consultar("SELECT * FROM usuarios WHERE rut = '12222222-5'", uno=True)
        assert ana["nombre"] == "Ana María Rojas Soto" and ana["apellidos"] == ""
        assert ana["fecha_ingreso"] == "2019" and ana["telefono"] == "+56911112222"
        assert luis["fecha_ingreso"] == "2020-01-01" and luis["email"] == "luis@ejemplo.cl"
        assert ana["usuario"] == "111111111" and ana["debe_cambiar_clave"] == 1
        assert base.consultar("SELECT id FROM usuarios WHERE nombre = 'Rut Malo'", uno=True) is None


def test_carga_de_cientos_de_socios_es_rapida_y_la_clave_inicial_sirve(cliente, app_prueba):
    import time
    import padron

    def rut_valido(cuerpo):
        suma, factor = 0, 2
        for digito in reversed(str(cuerpo)):
            suma += int(digito) * factor
            factor = 2 if factor == 7 else factor + 1
        dv = {10: "K", 11: "0"}.get(11 - suma % 11, str(11 - suma % 11))
        return f"{cuerpo}-{dv}"

    filas = [["N°", "Nombres", "Domicilio", "Carnet (RUT)"]] + [
        [n, f"Vecino {n}", "Tolson 1", rut_valido(10_000_000 + n)] for n in range(1, 401)
    ]
    contenido = xlsx_de_prueba([("ordenado por CORRELATIVO", filas)])
    ingresar(cliente)
    inicio = time.monotonic()
    respuesta = cliente.post(
        "/panel/cargar-padron",
        data={"archivo": (BytesIO(contenido), "padron.xlsx")},
        follow_redirects=True,
    )
    assert respuesta.status_code == 200
    # Con el cifrado fuerte por defecto esto tardaba ~30 s y el navegador lo daba por caído.
    assert time.monotonic() - inicio < 10
    with app_prueba.app_context():
        assert base.consultar(
            "SELECT COUNT(*) c FROM usuarios WHERE rol = 'socio' AND nombre LIKE 'Vecino %'",
            uno=True,
        )["c"] == 400
        fila = base.consultar("SELECT * FROM usuarios WHERE nombre = 'Vecino 7'", uno=True)
        assert fila["clave_hash"].startswith(padron.METODO_CLAVE_INICIAL)

    cliente.get("/logout")
    ingresar(cliente, fila["usuario"], padron.clave_inicial(fila["rut"]))
    assert cliente.get("/mis-solicitudes").headers["Location"].endswith("/cuenta/clave")
    cliente.post("/cuenta/clave", data={
        "clave_actual": padron.clave_inicial(fila["rut"]),
        "clave_nueva": "nueva-clave-segura", "confirmar_clave": "nueva-clave-segura"})
    with app_prueba.app_context():
        nueva = base.consultar("SELECT clave_hash FROM usuarios WHERE id = ?", (fila["id"],), uno=True)
        # La clave que elige la persona sí queda con el cifrado fuerte.
        assert not nueva["clave_hash"].startswith(padron.METODO_CLAVE_INICIAL)


def test_menu_del_panel_agrupado_y_plegable(cliente, app_prueba):
    with app_prueba.app_context():
        base.ejecutar("UPDATE mensajes SET leido = 1")
        base.ejecutar(
            "INSERT INTO mensajes (nombre, email, mensaje, fecha_envio) "
            "VALUES ('Vecina', 'vecina@ejemplo.cl', 'Consulta', '2026-10-27')"
        )
    ingresar(cliente)
    html = cliente.get("/panel/noticias").get_data(as_text=True)
    # Cada categoría es un grupo plegable; solo el de la página actual parte abierto.
    assert html.count('class="panel-menu-grupo"') >= 5
    assert 'data-grupo-menu="Información institucional" open data-grupo-actual' in html
    assert 'data-grupo-menu="Transparencia" >' in html
    assert 'aria-current="page"' in html
    # El grupo «Solicitudes» queda cerrado, pero muestra cuántas cosas esperan respuesta.
    assert 'title="Pendientes en esta sección">' in html
    assert re.search(
        r'Mensajes de contacto</span>\s*<span class="badge[^"]*">1</span>', html
    )


@pytest.mark.parametrize("valor, esperado", [
    ("2019", "2019"), ("2019.0", "2019"), ("43831", "2020-01-01"),
    ("2015 / 03-04", "2015"), ("15-03-2018", "2018-03-15"), ("", ""), ("sin dato", ""),
])
def test_fecha_de_ingreso_del_padron(valor, esperado):
    import padron
    assert padron.normalizar_fecha_ingreso(valor) == esperado


def test_primer_ingreso_exige_cambiar_clave_y_completar_correo(cliente, app_prueba):
    crear_socio(app_prueba, usuario="111111111", email="", rut="11111111-1",
                clave_hash=generate_password_hash("11111111"), debe_cambiar_clave=1)
    ingresar(cliente, "111111111", "11111111")
    assert cliente.get("/mis-solicitudes").headers["Location"].endswith("/cuenta/clave")
    cliente.post("/cuenta/clave", data={
        "clave_actual": "11111111", "clave_nueva": "nueva-clave-segura",
        "confirmar_clave": "nueva-clave-segura"})
    assert cliente.get("/mis-solicitudes").headers["Location"].endswith("/cuenta/datos")
    error = cliente.post("/cuenta/datos", data={"email": "sin-arroba"}, follow_redirects=True)
    assert "correo electrónico válido" in error.get_data(as_text=True)
    cliente.post("/cuenta/datos", data={
        "email": "ana@ejemplo.cl", "telefono": "912345678", "fecha_nacimiento": "1956-03-04"})
    assert cliente.get("/mis-solicitudes").status_code == 200
    with app_prueba.app_context():
        fila = base.consultar("SELECT * FROM usuarios WHERE usuario = '111111111'", uno=True)
        assert fila["email"] == "ana@ejemplo.cl" and fila["telefono"] == "+56912345678"
        assert fila["fecha_nacimiento"] == "1956-03-04"


def test_produccion_no_arranca_con_claves_de_prueba(app_prueba, monkeypatch):
    import app as modulo

    monkeypatch.setitem(modulo.app.config, "EN_PRODUCCION", True)
    with pytest.raises(RuntimeError) as error:
        modulo.verificar_configuracion_produccion()
    assert "SECRET_KEY" in str(error.value) and "ADMIN_PASSWORD" in str(error.value)
    monkeypatch.setitem(modulo.app.config, "SECRET_KEY", "x" * 40)
    monkeypatch.setitem(modulo.app.config, "ADMIN_PASSWORD", "una-clave-larga-y-real")
    monkeypatch.setitem(modulo.app.config, "ADMIN_EMAIL", "directiva@ejemplo.cl")
    monkeypatch.setitem(modulo.app.config, "CREAR_COORDINADOR", False)
    modulo.verificar_configuracion_produccion()


def test_base_de_produccion_sin_datos_de_ejemplo(app_prueba, monkeypatch):
    import app as modulo

    carpeta = tempfile.mkdtemp()
    monkeypatch.setattr(base, "INSTANCE_DIR", carpeta)
    monkeypatch.setattr(base, "DB_PATH", os.path.join(carpeta, "produccion.db"))
    monkeypatch.setitem(modulo.app.config, "DATOS_EJEMPLO", False)
    monkeypatch.setitem(modulo.app.config, "CREAR_COORDINADOR", False)
    base.init_db(modulo.app)
    with modulo.app.app_context():
        assert base.consultar("SELECT COUNT(*) c FROM noticias", uno=True)["c"] == 0
        assert base.consultar("SELECT COUNT(*) c FROM movimientos", uno=True)["c"] == 0
        assert base.consultar("SELECT COUNT(*) c FROM puntos_mapa", uno=True)["c"] > 0
        assert base.consultar("SELECT id FROM documentos WHERE titulo LIKE '%19.418%'", uno=True)
        roles = [f["rol"] for f in base.consultar("SELECT rol FROM usuarios")]
        assert roles == ["administrador"]


def test_reserva_exige_aceptar_el_aporte(cliente, app_prueba):
    fecha = (fecha_local() + timedelta(days=40)).isoformat()
    datos = {"fecha": fecha, "hora_inicio": "10:00", "hora_fin": "12:00",
             "actividad": "Reunión sin aporte", "solicitante": "X", "telefono": "+56911112222"}
    cliente.post("/sede/reservar", data=datos)
    with app_prueba.app_context():
        assert base.consultar("SELECT id FROM reservas WHERE actividad = 'Reunión sin aporte'", uno=True) is None
    cliente.post("/sede/reservar", data=dict(datos, acepta_aporte="on"))
    with app_prueba.app_context():
        fila = base.consultar("SELECT * FROM reservas WHERE actividad = 'Reunión sin aporte'", uno=True)
        assert fila["acepta_aporte"] == 1
    assert "Aporte por el uso de la sede" in cliente.get("/sede").get_data(as_text=True)


def test_formularios_se_autocompletan_y_arman_la_direccion(cliente, app_prueba):
    crear_socio(app_prueba, rut="11111111-1", direccion="Los Boldos 45", telefono="+56911112222")
    ingresar(cliente, "socia_prueba", "clave-socia-123")
    formulario = cliente.get("/certificado-residencia").get_data(as_text=True)
    assert 'value="11.111.111-1"' in formulario
    assert '<option value="Los Boldos" selected>' in formulario and 'value="45"' in formulario
    cliente.post("/certificado-residencia", data={
        "nombre": "Socia Prueba", "rut": "11.111.111-1", "calle": "Tolson", "numero": "88",
        "motivo_tipo": "Trabajo", "motivo": "contrato nuevo", "entiendo_revision": "on",
        "documento_domicilio": (BytesIO(b"boleta"), "boleta.pdf"),
    })
    with app_prueba.app_context():
        fila = base.consultar("SELECT * FROM certificados WHERE nombre = 'Socia Prueba'", uno=True)
        assert fila["direccion"] == "Tolson 88" and fila["motivo"] == "Trabajo: contrato nuevo"


def test_socio_pausa_su_emprendimiento(cliente, app_prueba):
    socia = crear_socio(app_prueba)
    ingresar(cliente, "socia_prueba", "clave-socia-123")
    cliente.post("/inscribir-servicio", data={
        "nombre_servicio": "Kuchen de la Socia", "vecino": "Socia Prueba", "rubro": "Alimentación",
        "telefono": "+56911112222", "calle": "Tolson", "numero": "5"})
    with app_prueba.app_context():
        servicio = base.consultar("SELECT * FROM servicios WHERE nombre_servicio = 'Kuchen de la Socia'", uno=True)
        assert servicio["usuario_id"] == socia and servicio["direccion"] == "Tolson 5"
        base.ejecutar("UPDATE servicios SET aprobado = 1 WHERE id = ?", (servicio["id"],))
    assert "Kuchen de la Socia" in cliente.get("/directorio").get_data(as_text=True)
    cliente.post(f"/mis-emprendimientos/{servicio['id']}/estado", data={"accion": "pausar"})
    assert "Kuchen de la Socia" not in cliente.get("/directorio").get_data(as_text=True)
    cliente.get("/logout")
    crear_socio(app_prueba, usuario="otra_socia", email="otra@ejemplo.cl")
    ingresar(cliente, "otra_socia", "clave-socia-123")
    ajeno = cliente.post(f"/mis-emprendimientos/{servicio['id']}/estado", data={"accion": "reactivar"})
    assert ajeno.status_code == 404


def test_avisos_del_sitio_respetan_su_vigencia(cliente):
    ingresar(cliente)
    hoy = fecha_local()
    cliente.post("/panel/avisos/nuevo", data={
        "titulo": "Corte de agua el martes", "tipo": "Importante", "activo": "on"})
    cliente.post("/panel/avisos/nuevo", data={
        "titulo": "Aviso vencido", "tipo": "Informativo", "activo": "on",
        "fecha_fin": (hoy - timedelta(days=1)).isoformat()})
    inicio = cliente.get("/").get_data(as_text=True)
    assert "Corte de agua el martes" in inicio and "Aviso vencido" not in inicio


def test_mapa_se_administra_desde_el_panel(cliente):
    ingresar(cliente)
    sin_coordenadas = cliente.post("/panel/mapa/nuevo", data={
        "titulo": "Punto sin ubicación", "categoria": "Comercio", "latitud": "", "longitud": ""},
        follow_redirects=True)
    assert sin_coordenadas.status_code == 200 and "Revisa estos campos" in sin_coordenadas.get_data(as_text=True)
    cliente.post("/panel/mapa/nuevo", data={
        "titulo": "Almacén <b>Don Lucho</b>", "categoria": "Comercio",
        "latitud": "-33,0301", "longitud": "-71.5870", "activo": "on", "orden": "9"})
    pagina = cliente.get("/movilidad").get_data(as_text=True)
    assert "Don Lucho" in pagina and "-33.0301" in pagina
    assert "<b>Don Lucho</b>" not in pagina
    assert "Escalera Chirimoyo" in pagina


def test_cumpleanos_por_dia_y_semana(cliente, app_prueba):
    manana = fecha_local() + timedelta(days=1)
    crear_socio(app_prueba, usuario="cumple", email="c@ejemplo.cl", nombre="Celebra",
                fecha_nacimiento=manana.replace(year=1960).isoformat())
    ingresar(cliente)
    assert "Celebra" not in cliente.get("/panel/socios?cumple=hoy").get_data(as_text=True).split("Filtro de Búsqueda")[0]
    assert "Celebra" in cliente.get("/panel/socios?cumple=manana").get_data(as_text=True)
    assert "Celebra" in cliente.get("/panel/socios?cumple=semana").get_data(as_text=True)


def test_cumpleanos_29_de_febrero_en_anio_no_bisiesto(app_prueba):
    import app as modulo

    crear_socio(app_prueba, usuario="bisiesto", email="b@ejemplo.cl", nombre="Bisiesta",
                fecha_nacimiento="1964-02-29")
    with app_prueba.test_request_context():
        assert [s["nombre"] for s in modulo.cumpleaneros_de(date(2027, 2, 28))] == ["Bisiesta"]
        assert modulo.cumpleaneros_de(date(2028, 2, 28)) == []


def test_cambios_de_estado_avisan_por_correo(cliente, app_prueba, monkeypatch):
    import correo

    enviados = []
    monkeypatch.setattr(correo, "enviar", lambda a, asunto, cuerpo: enviados.append((a, asunto, cuerpo)) or True)
    fecha = (fecha_local() + timedelta(days=45)).isoformat()
    cliente.post("/sede/reservar", data={
        "fecha": fecha, "hora_inicio": "10:00", "hora_fin": "12:00", "actividad": "Once vecinal",
        "solicitante": "Vecina", "telefono": "+56911112222", "email": "vecina@ejemplo.cl",
        "acepta_aporte": "on"})
    assert any("Nueva solicitud de reserva" in e[1] for e in enviados)
    with app_prueba.app_context():
        reserva = base.consultar("SELECT id FROM reservas WHERE actividad = 'Once vecinal'", uno=True)
    ingresar(cliente)
    cliente.post(f"/panel/reservas/{reserva['id']}/estado", data={"estado": "Aprobada"})
    aviso = [e for e in enviados if e[0] == "vecina@ejemplo.cl"]
    assert aviso and "Aprobada" in aviso[0][1] and "aporte" in aviso[0][2]


def test_recuperar_clave_con_enlace_de_un_solo_uso(cliente, app_prueba, monkeypatch):
    import correo

    enviados = []
    monkeypatch.setattr(correo, "configurado", lambda: True)
    monkeypatch.setattr(correo, "enviar", lambda a, asunto, cuerpo: enviados.append(cuerpo) or True)
    crear_socio(app_prueba, rut="11111111-1")
    cliente.post("/recuperar-clave", data={"dato": "nadie@ejemplo.cl"})
    assert enviados == []
    cliente.post("/recuperar-clave", data={"dato": "11.111.111-1"})
    enlace = [l for l in enviados[0].splitlines() if "/recuperar-clave/" in l][0]
    ruta = enlace[enlace.index("/recuperar-clave/"):]
    assert "La clave no puede ser el número de tu RUT" in cliente.post(ruta, data={
        "clave_nueva": "11111111", "confirmar_clave": "11111111"}).get_data(as_text=True)
    cliente.post(ruta, data={"clave_nueva": "otra-clave-nueva", "confirmar_clave": "otra-clave-nueva"})
    assert cliente.get(ruta).status_code == 302
    respuesta = ingresar(cliente, "socia_prueba", "otra-clave-nueva")
    assert "Portal del Socio" in respuesta.get_data(as_text=True)


def test_registro_de_socios_bajas_y_descargas(cliente, app_prueba):
    hoy = fecha_local()
    crear_socio(app_prueba, usuario="vigente", email="v@ejemplo.cl", nombre="Vigente",
                numero_socio="10", rut="11111111-1", fecha_ingreso=hoy.isoformat())
    retirada = crear_socio(app_prueba, usuario="retirada", email="r@ejemplo.cl", nombre="Retirada",
                           numero_socio="11", rut="12222222-5")
    ingresar(cliente)
    cliente.post(f"/panel/socios/{retirada}/baja", data={
        "motivo_baja": "Cambio de domicilio", "fecha_baja": hoy.isoformat()})
    registro = cliente.get("/panel/socios/registro.csv").get_data(as_text=True)
    assert "Vigente" in registro and "11.111.111-1" in registro and "Retirada" not in registro
    movimientos = cliente.get("/panel/socios/altas-bajas.csv").get_data(as_text=True)
    assert "Incorporación;" in movimientos and "Retiro;" in movimientos
    assert "Cambio de domicilio" in movimientos
    cliente.post(f"/panel/socios/{retirada}/reincorporar")
    with app_prueba.app_context():
        fila = base.consultar("SELECT * FROM usuarios WHERE id = ?", (retirada,), uno=True)
        assert fila["activo"] == 1 and fila["motivo_baja"] == ""


def test_respaldo_solo_para_administracion(cliente):
    ingresar(cliente, "coordinador", "esperanza2026")
    assert cliente.get("/panel/respaldo").status_code == 403
    cliente.get("/logout")
    ingresar(cliente)
    respuesta = cliente.get("/panel/respaldo")
    assert respuesta.status_code == 200
    with zipfile.ZipFile(BytesIO(respuesta.data)) as archivo:
        assert "junta.db" in archivo.namelist()


def test_total_de_socios_sale_del_padron(cliente, app_prueba):
    crear_socio(app_prueba, usuario="s1", email="s1@ejemplo.cl")
    crear_socio(app_prueba, usuario="s2", email="s2@ejemplo.cl", fallecido=1)
    with app_prueba.app_context():
        total = base.consultar(
            "SELECT COUNT(*) c FROM usuarios WHERE rol = 'socio' AND activo = 1 AND fallecido = 0 "
            "AND estado_aprobacion = 'Aprobada'", uno=True)["c"]
    assert f'<div class="stat-number">{total}</div>' in cliente.get("/").get_data(as_text=True)
