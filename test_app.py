"""Pruebas del sitio de la Junta de Vecinos N.° 2 · Cerro Esperanza."""

import os
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
            "actividad": "Actividad en el pasado", "solicitante": "X", "telefono": "1",
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
            "actividad": "Choque con adulto mayor", "solicitante": "X", "telefono": "1",
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
            "actividad": "Fuera de horario", "solicitante": "X", "telefono": "1",
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
    assert "pendiente de aprobación" in respuesta_login.get_data(as_text=True).lower()
    ingresar(cliente)
    cliente.post(f"/panel/usuarios/{cuenta['id']}/aprobar")
    cliente.get("/logout")
    assert ingresar(cliente, "vecina_cert", "clave-segura-123").status_code == 200
    assert cliente.get("/mis-solicitudes").status_code == 200


def test_inscripcion_de_servicio_queda_sin_aprobar(cliente, app_prueba):
    cliente.post(
        "/directorio/inscribir",
        data={
            "nombre_servicio": "Costurería Ana", "vecino": "Ana", "rubro": "Otros",
            "telefono": "999", "whatsapp": "56999999999",
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
    cliente.post(
        "/directorio/inscribir",
        data={"nombre_servicio": "Pan de prueba", "vecino": "Vecino", "rubro": "Otros",
              "telefono": "123"},
    )
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
        data={"nombre": "Vecina Nueva", "usuario": "vecina", "email": "vecina@ejemplo.cl",
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
    cliente.post("/panel/permisos-coordinador", data={"crear_vecinos": "on"})
    cliente.get("/logout")
    ingresar(cliente, "coordinador", "esperanza2026")
    assert cliente.get("/panel/crear-vecino").status_code == 200
    cliente.post(
        "/panel/crear-vecino",
        data={"nombre": "Vecino coordinado", "usuario": "vecino_coord", "email": "",
              "clave": "clave-segura", "confirmar_clave": "clave-segura"},
    )
    with app_prueba.app_context():
        fila = base.consultar(
            "SELECT * FROM usuarios WHERE usuario = 'vecino_coord'", uno=True
        )
        assert fila["estado_aprobacion"] == "Pendiente" and not fila["activo"]
    assert cliente.get("/panel/usuarios-pendientes", follow_redirects=False).status_code == 302


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
        data={"nombre": "Vecino lector", "usuario": "vecino_lector", "email": "",
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




