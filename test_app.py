import pytest

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.mark.parametrize(
    "ruta",
    [
        "/",
        "/noticias",
        "/proyectos",
        "/transparencia",
        "/directiva",
        "/contacto",
    ],
)
def test_paginas_publicas_responden_ok(client, ruta):
    respuesta = client.get(ruta)
    assert respuesta.status_code == 200


def test_login_requerido_para_admin(client):
    respuesta = client.get("/admin/", follow_redirects=False)
    assert respuesta.status_code == 302
    assert "/login" in respuesta.headers["Location"]
