"""Asistente del inicio: entiende lo que escribe una vecina o vecino y sugiere
a qué página del sitio ir.

No usa inteligencia artificial externa. Compara el texto con una lista curada de
destinos y sus palabras clave, tolerando acentos, plurales y errores de tipeo.
Así nunca inventa información ni envía a terceros lo que escriben los vecinos.
Para que entienda una frase nueva basta con agregarla a ``claves``.
"""

import difflib
import re
import unicodedata

LARGO_MAXIMO = 300
MAXIMO_SUGERENCIAS = 3
SIMILITUD_MINIMA = 0.82  # tolerancia a errores de tipeo ("sertificado")

PUNTOS_FRASE = 3
PUNTOS_PALABRA = 2
PUNTOS_PARECIDA = 1.5

# Cada destino es una página del sitio. ``claves`` acepta palabras sueltas o
# frases completas; se escriben normalmente (con acentos) y se normalizan solas.
DESTINOS = [
    {
        "id": "certificado",
        "endpoint": "certificado",
        "titulo": "Certificado de residencia",
        "respuesta": "Puedes pedir tu certificado de residencia en línea. La directiva "
                     "revisa la solicitud y te entrega un código para seguir su avance.",
        "claves": [
            "certificado", "residencia", "domicilio", "constancia", "papel",
            "acreditar domicilio", "que vivo aquí", "que vivo en el cerro",
            "comprobante de domicilio", "certificado de residencia",
        ],
    },
    {
        "id": "estado_certificado",
        "endpoint": "estado_certificado",
        "titulo": "Estado de mi certificado",
        "respuesta": "Con el código de seguimiento que recibiste puedes revisar en qué va tu solicitud.",
        "claves": [
            "seguimiento", "código", "en qué va", "cómo va", "está listo",
            "estado de mi certificado", "estado de mi solicitud", "ya salió",
        ],
    },
    {
        "id": "sede",
        "endpoint": "sede",
        "titulo": "Disponibilidad de la sede",
        "respuesta": "En el calendario de la sede ves qué días y horarios están libres u ocupados.",
        "claves": [
            "sede", "disponible", "disponibilidad", "libre", "ocupada", "calendario",
            "agenda", "horario", "está libre", "qué días",
        ],
    },
    {
        "id": "reservar",
        "endpoint": "reservar",
        "titulo": "Pedir la sede",
        "respuesta": "Puedes solicitar la sede para una actividad. Queda pendiente "
                     "hasta que la directiva la confirme.",
        "claves": [
            "reservar", "reserva", "arrendar", "arriendo", "arrienda", "prestar",
            "pedir la sede", "usar la sede", "ocupar la sede", "cumpleaños", "fiesta",
            "celebración", "celebrar", "evento", "bingo", "completada",
        ],
    },
    {
        "id": "transparencia",
        "endpoint": "transparencia",
        "titulo": "Transparencia: ingresos y gastos",
        "respuesta": "En Transparencia están los ingresos, los gastos y las rendiciones "
                     "de cuentas de la junta.",
        "claves": [
            "gasto", "gastó", "plata", "dinero", "rendición", "las cuentas",
            "balance", "ingreso", "presupuesto", "tesorería", "auditoría", "fondo",
            "en qué se gastó", "rendición de cuentas", "transparencia",
        ],
    },
    {
        "id": "proyectos",
        "endpoint": "proyectos",
        "titulo": "Proyectos del cerro",
        "respuesta": "Aquí están los proyectos postulados, aprobados y en ejecución, con su avance.",
        "claves": [
            "proyecto", "obra", "plaza", "mejoramiento",
            "postulación", "fondeve", "avance", "luminaria", "cámara", "escalera",
        ],
    },
    {
        "id": "plan_maestro",
        "endpoint": "plan_maestro",
        "titulo": "Plan maestro",
        "respuesta": "El plan maestro reúne lo que la comunidad proyecta para el cerro a largo plazo.",
        "claves": ["plan maestro", "futuro", "largo plazo", "visión", "planificación"],
    },
    {
        "id": "documentos",
        "endpoint": "documentos",
        "titulo": "Documentos y estatutos",
        "respuesta": "Ahí encuentras los estatutos, la normativa y las actas de asamblea.",
        "claves": [
            "estatuto", "reglamento", "ley", "normativa", "acta", "documento",
        ],
    },
    {
        "id": "noticias",
        "endpoint": "noticias",
        "titulo": "Noticias y actividades",
        "respuesta": "Revisa las últimas noticias, asambleas y actividades del barrio.",
        "claves": [
            "noticia", "novedad", "actividad", "asamblea", "reunión", "taller",
            "operativo", "aviso", "qué está pasando", "qué hay",
        ],
    },
    {
        "id": "quienes_somos",
        "endpoint": "quienes_somos",
        "titulo": "Quiénes somos y directiva",
        "respuesta": "Conoce a la directiva actual y cómo funciona la junta de vecinos.",
        "claves": [
            "directiva", "presidente", "presidenta", "secretario", "secretaria",
            "tesorero", "tesorera", "dirigente", "quiénes son",
            "quiénes somos", "quién manda",
        ],
    },
    {
        "id": "historia",
        "endpoint": "historia",
        "titulo": "Historia y memoria del cerro",
        "respuesta": "Un recorrido por la historia y la memoria del Cerro Esperanza.",
        "claves": ["historia", "memoria", "fundación", "antiguo", "origen", "fotos antiguas"],
    },
    {
        "id": "como_asociarse",
        "endpoint": "como_asociarse",
        "titulo": "Cómo hacerse socia o socio",
        "respuesta": "Te explicamos los requisitos y pasos para asociarte a la junta.",
        "claves": [
            "socio", "socia", "asociarme", "asociarse", "inscribirme", "inscripción",
            "requisito", "cuota", "unirme", "ser parte", "hacerme socio", "hacerme socia",
        ],
    },
    {
        "id": "login",
        "endpoint": "login",
        "titulo": "Ingresar a mi cuenta",
        "respuesta": "Si ya eres socia o socio con cuenta, ingresa aquí con tu usuario y clave.",
        "claves": [
            "ingresar", "entrar", "iniciar sesión", "contraseña", "clave", "mi cuenta",
            "olvidé mi clave",
        ],
    },
    {
        "id": "registro",
        "endpoint": "registro",
        "titulo": "Solicitar cuenta de socio",
        "respuesta": "Si estás en el padrón de socios, puedes solicitar una cuenta en el sitio.",
        "claves": ["crear cuenta", "registrarme", "registro", "cuenta nueva", "abrir cuenta"],
    },
    {
        "id": "mis_menores",
        "endpoint": "mis_menores",
        "titulo": "Censo infantil / juguetes de Navidad",
        "respuesta": "Las socias y socios registran aquí a los menores de 10 años para la "
                     "entrega navideña. Necesitas ingresar con tu cuenta.",
        "claves": [
            "juguete", "navidad", "censo", "regalo", "pascua",
            "niño", "niña", "hijo", "hija", "censo infantil",
        ],
    },
    {
        "id": "directorio",
        "endpoint": "directorio",
        "titulo": "Servicios y emprendimientos de vecinos",
        "respuesta": "En el directorio están los servicios y emprendimientos de vecinas y vecinos del cerro.",
        "claves": [
            "servicio", "emprendimiento", "comprar", "directorio", "oficio", "dato",
        ],
    },
    {
        "id": "inscribir_servicio",
        "endpoint": "inscribir_servicio",
        "titulo": "Inscribir mi emprendimiento",
        "respuesta": "Si eres socia o socio, puedes publicar tu servicio o emprendimiento en el directorio.",
        "claves": [
            "inscribir mi", "publicar mi", "ofrecer", "vender", "mi negocio",
            "mi emprendimiento", "mi servicio", "emprendedora", "emprendedor",
        ],
    },
    {
        "id": "movilidad",
        "endpoint": "movilidad",
        "titulo": "Movilidad y accesibilidad",
        "respuesta": "Información de micros, recorridos, accesos y mapa del cerro.",
        "claves": [
            "micro", "bus", "colectivo", "locomoción", "transporte",
            "ascensor", "paradero", "recorrido", "accesibilidad", "silla de ruedas",
            "mapa", "cómo llegar", "llegar al cerro",
        ],
    },
    {
        "id": "contacto",
        "endpoint": "contacto",
        "titulo": "Contactar a la directiva",
        "respuesta": "Escríbenos un mensaje y la directiva te responderá.",
        "claves": [
            "contacto", "contactar", "hablar", "escribir", "correo", "teléfono",
            "reclamo", "denuncia", "sugerencia", "consulta", "queja", "problema",
            "whatsapp", "hablar con alguien",
        ],
    },
]

# Búsquedas de un oficio concreto: llevan al directorio ya filtrado por rubro.
RUBROS = [
    ("Alimentación", ["pan", "amasado", "empanada", "comida", "colación", "almuerzo",
                      "torta", "kuchen", "queque", "pastel", "sopaipilla"]),
    ("Almacén y comercio", ["almacén", "verdulería", "negocio de barrio", "minimarket",
                            "abarrotes", "gas licuado"]),
    ("Belleza y cuidado personal", ["peluquería", "peluquera", "corte de pelo", "uñas",
                                    "manicure", "barbería", "barbero", "depilación"]),
    ("Construcción y oficios", ["gasfiter", "gásfiter", "electricista", "maestro chasquilla",
                                "pintor", "carpintero", "soldador", "albañil", "techo",
                                "gotera", "cerrajero", "arreglo", "reparación"]),
    ("Cuidado de personas", ["cuidadora", "cuidador", "niñera", "cuidar"]),
    ("Educación y clases", ["clase", "reforzamiento", "profesor", "profesora",
                            "tarea", "inglés", "matemática"]),
    ("Salud", ["kinesiólogo", "enfermera", "enfermero", "podóloga", "podólogo",
               "masaje", "inyección"]),
    ("Transporte y fletes", ["flete", "mudanza", "traslado", "camioneta"]),
]

for _rubro, _claves in RUBROS:
    DESTINOS.append({
        "id": "rubro:" + _rubro,
        "endpoint": "directorio",
        "args": {"rubro": _rubro},
        "titulo": f"Servicios de vecinos: {_rubro}",
        "respuesta": f"Estos son los vecinos que ofrecen servicios de {_rubro.lower()} en el cerro.",
        "claves": _claves,
        "puntos_extra": 0.5,  # gana al directorio general cuando se nombra un oficio
    })

POPULARES = ["certificado", "sede", "transparencia", "contacto"]

PALABRAS_VACIAS = {
    "a", "al", "algo", "como", "con", "cual", "de", "del", "donde", "el", "en", "es",
    "esta", "favor", "hay", "la", "las", "lo", "los", "me", "mi", "mis", "necesito",
    "o", "para", "por", "puedo", "que", "quiero", "saber", "se", "si", "su", "tengo",
    "un", "una", "uno", "ver", "y", "ya", "yo", "le", "les", "hacer", "pedir",
    "solicitar", "buscar", "busco", "informacion", "ayuda", "hola", "buenas", "tardes",
    "dias", "noches", "buenos", "gracias", "cuando", "quien", "nos", "eso", "este",
}

SALUDOS = {"hola", "buenas", "buenos", "alo", "holi"}
AGRADECIMIENTOS = {"gracias", "agradecido", "agradecida", "muchas gracias"}
EMERGENCIAS = [
    "emergencia", "robo", "robaron", "asalto", "incendio", "accidente",
    "ambulancia", "carabineros", "bomberos", "herido", "herida", "balazos",
]

MENSAJE_EMERGENCIA = (
    "Si es una emergencia, llama de inmediato: 133 Carabineros, 131 Ambulancia (SAMU) "
    "o 132 Bomberos. Después puedes avisar a la directiva."
)


def normalizar(texto):
    """Minúsculas, sin acentos ni signos; conserva la ñ como n."""
    texto = unicodedata.normalize("NFD", str(texto or "").lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def formas(palabra):
    """La palabra y su posible singular, para que «gastos» encuentre «gasto».

    Las claves se escriben en singular; solo el texto del vecino se expande.
    """
    resultado = {palabra}
    if palabra.endswith("es") and len(palabra) >= 5:
        resultado.add(palabra[:-2])
    if palabra.endswith("s") and len(palabra) >= 4:
        resultado.add(palabra[:-1])
    return resultado


def _preparar(destino):
    frases, palabras = [], set()
    for clave in destino["claves"]:
        normal = normalizar(clave)
        if " " in normal:
            frases.append(normal)
        elif normal:
            palabras.add(normal)
    destino["_frases"] = frases
    destino["_palabras"] = palabras
    return destino


for _destino in DESTINOS:
    _preparar(_destino)


def _puntaje(destino, texto, raices, sueltas):
    puntos = 0.0
    for frase in destino["_frases"]:
        if f" {frase} " in f" {texto} ":
            puntos += PUNTOS_FRASE
    for palabra in destino["_palabras"]:
        if palabra in raices:
            puntos += PUNTOS_PALABRA
        elif len(palabra) >= 5 and any(
            len(p) >= 5 and difflib.SequenceMatcher(None, p, palabra).ratio() >= SIMILITUD_MINIMA
            for p in sueltas
        ):
            puntos += PUNTOS_PARECIDA
    if puntos:
        puntos += destino.get("puntos_extra", 0)
    return puntos


def _destinos_por_id(ids):
    return [d for i in ids for d in DESTINOS if d["id"] == i]


def sugerir(pregunta):
    """Devuelve ``{"tipo", "mensaje", "destinos"}`` para lo que escribió el vecino.

    ``tipo`` es "resultado", "emergencia", "saludo", "gracias", "vacio" o
    "sin_resultado". ``destinos`` son diccionarios de :data:`DESTINOS`.
    """
    texto = normalizar((pregunta or "")[:LARGO_MAXIMO])
    if not texto:
        return {
            "tipo": "vacio",
            "mensaje": "Cuéntame con tus palabras qué necesitas, o elige una opción:",
            "destinos": _destinos_por_id(POPULARES),
        }

    palabras = texto.split()
    sueltas = [p for p in palabras if p not in PALABRAS_VACIAS]
    raices = set().union(*(formas(p) for p in sueltas))

    if any(f" {normalizar(e)} " in f" {texto} " for e in EMERGENCIAS):
        return {
            "tipo": "emergencia",
            "mensaje": MENSAJE_EMERGENCIA,
            "destinos": _destinos_por_id(["contacto"]),
        }

    puntajes = sorted(
        ((_puntaje(d, texto, raices, sueltas), orden, d) for orden, d in enumerate(DESTINOS)),
        key=lambda t: (-t[0], t[1]),
    )
    mejor = puntajes[0][0]
    if mejor > 0:
        elegidos = [
            d for puntos, _, d in puntajes if puntos > 0 and puntos >= mejor * 0.5
        ][:MAXIMO_SUGERENCIAS]
        if len(elegidos) == 1:
            mensaje = "Esta es la página que necesitas:"
        else:
            mensaje = "Encontré estas opciones. Toca la que necesitas:"
        return {"tipo": "resultado", "mensaje": mensaje, "destinos": elegidos}

    if set(palabras) & AGRADECIMIENTOS:
        return {
            "tipo": "gracias",
            "mensaje": "¡De nada! Si necesitas algo más, aquí estoy.",
            "destinos": [],
        }
    if set(palabras) & SALUDOS:
        return {
            "tipo": "saludo",
            "mensaje": "¡Hola! Cuéntame qué necesitas y te llevo a la página correcta. "
                       "Por ejemplo: «quiero pedir la sede» o «¿en qué se gastó la plata?».",
            "destinos": _destinos_por_id(POPULARES),
        }
    return {
        "tipo": "sin_resultado",
        "mensaje": "No estoy seguro de haber entendido. Prueba con otras palabras, "
                   "elige una de estas opciones o escríbele a la directiva:",
        "destinos": _destinos_por_id(POPULARES),
    }
