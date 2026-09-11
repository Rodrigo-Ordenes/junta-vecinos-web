"""Pruebas del sitio de la Junta de Vecinos N.° 2 · Cerro Esperanza."""

import os
import tempfile
from datetime import date, timedelta

import pytest

import db as base


@pytest.fixture()
def app_prueba(monkeypatch):
    carpeta = tempfile.mkdtemp()
    monkeypatch.setattr(base, "INSTANCE_DIR", carpeta)
    monkeypatch.setattr(base, "DB_PATH", os.path.join(carpeta, "prueba.db"))
    import app as modulo

    modulo.app.config["TESTING"] = True
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
    otra = (date.today() + timedelta(days=7)).isoformat()
    assert cliente.get(f"/sede?semana={otra}").status_code == 200


# --- Formularios públicos --------------------------------------------------
def test_reserva_se_registra_como_pendiente(cliente, app_prueba):
    fecha = date.today() + timedelta(days=30)
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
    ayer = date.today() - timedelta(days=1)
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
    hoy = date.today()
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


def test_solicitud_de_certificado(cliente, app_prueba):
    respuesta = cliente.post(
        "/certificado-residencia",
        data={
            "nombre": "Juan Pérez", "rut": "11.111.111-1",
            "direccion": "Subida Esperanza 123", "telefono": "123", "email": "",
            "motivo": "Trámite municipal",
        },
        follow_redirects=True,
    )
    assert respuesta.status_code == 200
    with app_prueba.app_context():
        fila = base.consultar(
            "SELECT * FROM certificados WHERE rut = '11.111.111-1'", uno=True
        )
        assert fila is not None and fila["estado"] == "Recibida"


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


def test_vecino_se_registra_y_ve_sus_solicitudes(cliente):
    respuesta = cliente.post(
        "/registro",
        data={"nombre": "Vecina Nueva", "usuario": "vecina", "email": "vecina@ejemplo.cl",
              "clave": "clave123"},
        follow_redirects=True,
    )
    assert respuesta.status_code == 200
    assert cliente.get("/mis-solicitudes").status_code == 200
    # Un vecino no puede administrar
    assert cliente.get("/panel/noticias", follow_redirects=False).status_code == 302


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
        data={"fecha": date.today().isoformat(), "concepto": "Aporte de prueba",
              "tipo": "Ingreso", "monto": "50000"},
        follow_redirects=True,
    )
    with app_prueba.app_context():
        assert base.consultar(
            "SELECT * FROM movimientos WHERE concepto = 'Aporte de prueba'", uno=True
        ) is not None
    assert "Aporte de prueba" in cliente.get("/transparencia").get_data(as_text=True)


def test_textos_del_sitio_se_actualizan(cliente, app_prueba):
    ingresar(cliente)
    cliente.post(
        "/panel/contenido",
        data={"socios_inscritos": "2450", "mision": "Nueva misión", "vision": "",
              "historia": "", "direccion_sede": "", "horario_atencion": "",
              "email_contacto": "", "telefono_contacto": "", "whatsapp_grupo": "",
              "facebook": "", "instagram": "", "plan_maestro_intro": "",
              "requisitos_certificado": "Cédula de identidad"},
        follow_redirects=True,
    )
    assert "2450" in cliente.get("/").get_data(as_text=True)
    assert "Cédula de identidad" in cliente.get("/certificado-residencia").get_data(as_text=True)


def test_pagina_inexistente_muestra_error(cliente):
    assert cliente.get("/esta-pagina-no-existe").status_code == 404
