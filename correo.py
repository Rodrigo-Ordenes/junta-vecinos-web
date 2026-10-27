"""Envío de correos del sitio: avisos de trámites y recuperación de clave.

Se configura con variables de entorno (por ejemplo, una cuenta de Gmail con una
«contraseña de aplicación»):

    SMTP_HOST=smtp.gmail.com  SMTP_PORT=587
    SMTP_USUARIO=juntavecinos2@gmail.com  SMTP_CLAVE=<contraseña de aplicación>
    SMTP_REMITENTE="Junta de Vecinos N.° 2 <juntavecinos2@gmail.com>"

Sin esas variables el sitio funciona igual: los trámites se registran y solo se
anota en el registro (log) que el correo no se envió.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

registro = logging.getLogger(__name__)
FIRMA = "\n\n—\nJunta de Vecinos N.° 2 · Cerro Esperanza\nEste es un mensaje automático del sitio web."


def configurado():
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_REMITENTE"))


def enviar(destinatario, asunto, cuerpo):
    """Envía un correo de texto. Devuelve True si salió.

    Nunca lanza excepciones: si el correo falla, el trámite del vecino igual queda
    guardado y la directiva lo ve en el panel.
    """
    destinatario = (destinatario or "").strip()
    if not destinatario:
        return False
    if not configurado():
        registro.info("Correo no enviado (falta configurar SMTP): %s", asunto)
        return False
    mensaje = EmailMessage()
    mensaje["From"] = os.environ["SMTP_REMITENTE"]
    mensaje["To"] = destinatario
    mensaje["Subject"] = asunto
    mensaje.set_content(cuerpo + FIRMA)
    host = os.environ["SMTP_HOST"]
    puerto = int(os.environ.get("SMTP_PORT", "587"))
    usuario = os.environ.get("SMTP_USUARIO", "")
    clave = os.environ.get("SMTP_CLAVE", "")
    contexto = ssl.create_default_context()
    try:
        if puerto == 465:
            servidor = smtplib.SMTP_SSL(host, puerto, context=contexto, timeout=15)
        else:
            servidor = smtplib.SMTP(host, puerto, timeout=15)
            servidor.starttls(context=contexto)
        with servidor:
            if usuario:
                servidor.login(usuario, clave)
            servidor.send_message(mensaje)
        return True
    except (OSError, smtplib.SMTPException):
        registro.exception("No se pudo enviar el correo «%s»", asunto)
        return False
