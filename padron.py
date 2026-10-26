"""Carga masiva del padrón oficial de socios (.csv y .xlsx).

Uso desde la línea de comandos (dentro del entorno virtual del proyecto):

    python padron.py padron.csv            # importa los socios
    python padron.py padron.xlsx --simular # solo valida, no guarda nada

Columnas esperadas (en cualquier orden, con o sin tildes):

    numero_socio, rut, nombre, apellidos, direccion, telefono, email,
    fecha_nacimiento, fecha_ingreso, fallecido

Obligatorias: numero_socio, rut y nombre. Si no hay columna de apellidos, el
nombre completo queda en ``nombre``. También se reconocen los encabezados del
padrón oficial de la junta («N°», «Nombres», «Carnet (RUT)», «Domicilio»,
«F.Ingreso», «correo», «telefono»). En un libro con varias hojas se usa la primera
que tenga columnas de RUT y nombre; las demás columnas (estado civil,
nacionalidad, profesión, edad) se ignoran a propósito para no guardar datos
personales que el sitio no necesita.

Cada socio nuevo recibe una cuenta con rol ``socio``. El nombre de usuario es el
RUT sin puntos ni guion (por ejemplo ``123456789`` o ``12345678k``) y la clave
inicial son los dígitos del RUT sin dígito verificador (rellenados con ceros a la
izquierda hasta tener 8 caracteres). La cuenta queda marcada para exigir el cambio
de la clave en el primer ingreso.

No usa librerías externas: el lector de .xlsx se apoya en ``zipfile`` y
``xml.etree`` de la biblioteca estándar.
"""

import csv
import io
import re
import sys
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

from werkzeug.security import generate_password_hash

CAMPOS = (
    "numero_socio", "rut", "nombre", "apellidos", "direccion",
    "telefono", "email", "fecha_nacimiento", "fecha_ingreso", "fallecido",
)
OBLIGATORIOS = ("numero_socio", "rut", "nombre")
ALIAS_COLUMNAS = {
    "n_socio": "numero_socio", "nro_socio": "numero_socio", "numero": "numero_socio",
    "n_de_socio": "numero_socio", "socio": "numero_socio", "n": "numero_socio",
    "no": "numero_socio", "correlativo": "numero_socio",
    "run": "rut", "rol_unico_tributario": "rut", "carnet_rut": "rut", "carnet": "rut",
    "cedula": "rut", "carnet_run": "rut",
    "f_ingreso": "fecha_ingreso", "fecha_de_ingreso": "fecha_ingreso",
    "ingreso": "fecha_ingreso",
    "nombres": "nombre", "apellido": "apellidos",
    "domicilio": "direccion", "fono": "telefono", "celular": "telefono",
    "correo": "email", "correo_electronico": "email", "mail": "email",
    "nacimiento": "fecha_nacimiento", "fecha_de_nacimiento": "fecha_nacimiento",
    "fallecida": "fallecido", "difunto": "fallecido",
}
TAMANO_MAXIMO_ARCHIVO = 10 * 1024 * 1024
TAMANO_MAXIMO_XML = 40 * 1024 * 1024  # textos compartidos del libro
TAMANO_MAXIMO_HOJA = 400 * 1024 * 1024  # una hoja con formato aplicado a filas vacías pesa mucho
MAXIMO_FILAS = 20000
FILAS_VACIAS_PARA_CORTAR = 2000  # Excel suele "formatear" un millón de filas vacías
FILAS_PARA_BUSCAR_ENCABEZADO = 15
VALORES_VERDADEROS = {"1", "si", "s", "true", "verdadero", "x", "yes", "y"}
PATRON_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ErrorPadron(Exception):
    """El archivo no se pudo interpretar."""


# ---------------------------------------------------------------------------
# Utilidades de normalización
# ---------------------------------------------------------------------------
def solo_digitos(texto):
    return "".join(c for c in str(texto or "") if c.isdigit())


def telefono_chile(valor):
    """Normaliza un teléfono móvil chileno al formato +569XXXXXXXX ('' si no calza)."""
    digitos = solo_digitos(valor)
    if digitos.startswith("56"):
        digitos = digitos[2:]
    if len(digitos) == 8:
        digitos = "9" + digitos
    if len(digitos) == 9 and digitos.startswith("9"):
        return "+56" + digitos
    return ""


def _sin_acentos(texto):
    return "".join(
        c for c in unicodedata.normalize("NFD", str(texto or ""))
        if unicodedata.category(c) != "Mn"
    )


def normalizar_encabezado(texto):
    limpio = _sin_acentos(texto).lower().strip()
    limpio = re.sub(r"[^a-z0-9]+", "_", limpio).strip("_")
    return ALIAS_COLUMNAS.get(limpio, limpio)


def digito_verificador(cuerpo):
    suma, factor = 0, 2
    for digito in reversed(str(cuerpo)):
        suma += int(digito) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - suma % 11
    return "0" if resto == 11 else "K" if resto == 10 else str(resto)


def normalizar_rut(valor):
    """Devuelve el RUT como ``12345678-9`` o '' si no es válido."""
    limpio = re.sub(r"[^0-9kK]", "", str(valor or "")).upper()
    if len(limpio) < 2 or len(limpio) > 9:
        return ""
    cuerpo, dv = limpio[:-1], limpio[-1]
    if not cuerpo.isdigit() or int(cuerpo) == 0:
        return ""
    if digito_verificador(cuerpo) != dv:
        return ""
    return f"{int(cuerpo)}-{dv}"


def usuario_desde_rut(rut):
    return rut.replace("-", "").lower()


def clave_inicial(rut):
    """Clave inicial: dígitos del RUT sin dígito verificador, con mínimo de 8."""
    cuerpo = rut.split("-")[0]
    return cuerpo.zfill(8)


def normalizar_fecha(valor):
    """Acepta AAAA-MM-DD, DD-MM-AAAA, DD/MM/AAAA y números de serie de Excel."""
    texto = str(valor or "").strip()
    if not texto:
        return ""
    if re.fullmatch(r"\d+(\.\d+)?", texto) and 1 <= float(texto) < 80000:
        return (date(1899, 12, 30) + timedelta(days=int(float(texto)))).isoformat()
    for formato in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(texto[:10], formato).date().isoformat()
        except ValueError:
            continue
    return None


def normalizar_fecha_ingreso(valor):
    """Fecha de ingreso al registro: «AAAA-MM-DD» o, si solo se conoce el año, «AAAA».

    El padrón histórico mezcla años sueltos («2019»), fechas y anotaciones como
    «2019 / 03-15»; de estas últimas se conserva el año.
    """
    texto = str(valor or "").strip()
    if texto.endswith(".0"):
        texto = texto[:-2]
    if not texto:
        return ""
    if re.fullmatch(r"(19|20)\d\d", texto):
        return texto
    fecha = normalizar_fecha(texto)
    if fecha:
        return fecha
    anio = re.match(r"\s*((?:19|20)\d\d)\b", texto)
    return anio.group(1) if anio else ""


def es_verdadero(valor):
    return _sin_acentos(valor).strip().lower() in VALORES_VERDADEROS


# ---------------------------------------------------------------------------
# Lectura de archivos
# ---------------------------------------------------------------------------
def _leer_csv(contenido):
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = contenido.decode("latin-1")
    muestra = texto[:4096]
    try:
        dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t")
    except csv.Error:
        separador = ";" if muestra.count(";") > muestra.count(",") else ","

        class dialecto(csv.excel):
            delimiter = separador
    return [fila for fila in csv.reader(io.StringIO(texto), dialecto)]


def _indice_columna(referencia):
    letras = re.match(r"[A-Za-z]+", referencia or "")
    indice = 0
    for letra in (letras.group(0).upper() if letras else "A"):
        indice = indice * 26 + (ord(letra) - 64)
    return indice - 1


def _texto_de(nodo):
    return "".join(t.text or "" for t in nodo.iter() if t.tag.endswith("}t") or t.tag == "t")


def _es_etiqueta(elemento, nombre):
    return elemento.tag == nombre or elemento.tag.endswith("}" + nombre)


def _hojas_del_libro(archivo):
    """Rutas internas de las hojas en el orden en que aparecen en Excel."""
    nombres = archivo.namelist()
    try:
        libro = ET.fromstring(archivo.read("xl/workbook.xml"))
        relaciones = ET.fromstring(archivo.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ET.ParseError):
        libro = relaciones = None
    rutas = []
    if libro is not None:
        destinos = {
            r.get("Id"): r.get("Target", "").lstrip("/")
            for r in relaciones.iter() if _es_etiqueta(r, "Relationship")
        }
        for hoja in libro.iter():
            if not _es_etiqueta(hoja, "sheet"):
                continue
            rid = next((v for k, v in hoja.attrib.items() if k.endswith("}id")), None)
            destino = destinos.get(rid, "")
            ruta = destino if destino.startswith("xl/") else "xl/" + destino
            if ruta in nombres:
                rutas.append(ruta)
    if not rutas:
        rutas = sorted(
            (n for n in nombres if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
            key=lambda n: int(re.findall(r"\d+", n)[0]),
        )
    return rutas


def _filas_de_hoja(archivo, ruta, compartidas, limite=None):
    """Lee una hoja fila por fila sin cargarla completa en memoria."""
    filas, vacias = [], 0
    with archivo.open(ruta) as flujo:
        for _evento, fila in ET.iterparse(flujo):
            if not _es_etiqueta(fila, "row"):
                continue
            valores = {}
            for celda in fila:
                if not _es_etiqueta(celda, "c"):
                    continue
                tipo = celda.get("t")
                valor = ""
                if tipo == "inlineStr":
                    valor = _texto_de(celda)
                else:
                    for hijo in celda:
                        if _es_etiqueta(hijo, "v"):
                            valor = hijo.text or ""
                    if tipo == "s" and valor.isdigit() and int(valor) < len(compartidas):
                        valor = compartidas[int(valor)]
                    elif tipo == "b":
                        valor = "1" if valor == "1" else "0"
                if str(valor).strip():
                    valores[_indice_columna(celda.get("r"))] = valor
            fila.clear()
            if not valores:
                vacias += 1
                if filas and vacias >= FILAS_VACIAS_PARA_CORTAR:
                    break
                continue
            vacias = 0
            filas.append([valores.get(i, "") for i in range(max(valores) + 1)])
            if limite and len(filas) >= limite:
                break
            if len(filas) > MAXIMO_FILAS:
                raise ErrorPadron(f"La hoja tiene más de {MAXIMO_FILAS} filas con datos.")
    return filas


def _posicion_encabezado(filas):
    """Índice de la fila que tiene las columnas obligatorias, o None."""
    for indice, fila in enumerate(filas[:FILAS_PARA_BUSCAR_ENCABEZADO]):
        encabezados = {normalizar_encabezado(c) for c in fila}
        if all(c in encabezados for c in OBLIGATORIOS):
            return indice
    return None


def _leer_xlsx(contenido):
    try:
        archivo = zipfile.ZipFile(io.BytesIO(contenido))
    except zipfile.BadZipFile as error:
        raise ErrorPadron("El archivo .xlsx está dañado o no es un libro de Excel.") from error
    with archivo:
        for info in archivo.infolist():
            limite = TAMANO_MAXIMO_HOJA if info.filename.startswith("xl/worksheets/") else TAMANO_MAXIMO_XML
            if info.file_size > limite:
                raise ErrorPadron("El libro de Excel es demasiado grande.")
        hojas = _hojas_del_libro(archivo)
        if not hojas:
            raise ErrorPadron("El libro de Excel no contiene hojas.")
        compartidas = []
        if "xl/sharedStrings.xml" in archivo.namelist():
            raiz = ET.fromstring(archivo.read("xl/sharedStrings.xml"))
            compartidas = [_texto_de(si) for si in raiz if _es_etiqueta(si, "si")]
        # Se usa la primera hoja que tenga encabezados de padrón (un libro puede
        # traer antes hojas de resumen).
        for ruta in hojas:
            muestra = _filas_de_hoja(archivo, ruta, compartidas, limite=FILAS_PARA_BUSCAR_ENCABEZADO)
            posicion = _posicion_encabezado(muestra)
            if posicion is not None:
                return _filas_de_hoja(archivo, ruta, compartidas)[posicion:]
        return _filas_de_hoja(archivo, hojas[0], compartidas)


def leer_padron(contenido, nombre_archivo):
    """Convierte un .csv/.xlsx en una lista de diccionarios con las columnas del padrón."""
    if len(contenido) > TAMANO_MAXIMO_ARCHIVO:
        raise ErrorPadron("El archivo supera los 10 MB.")
    extension = nombre_archivo.rsplit(".", 1)[-1].lower() if "." in nombre_archivo else ""
    if extension == "csv":
        filas = _leer_csv(contenido)
    elif extension == "xlsx":
        filas = _leer_xlsx(contenido)
    else:
        raise ErrorPadron("Sube un archivo .csv o .xlsx.")
    filas = [f for f in filas if any(str(c).strip() for c in f)]
    if not filas:
        raise ErrorPadron("El archivo no tiene datos.")
    posicion = _posicion_encabezado(filas)
    if posicion:
        filas = filas[posicion:]
    encabezados = [normalizar_encabezado(c) for c in filas[0]]
    faltantes = [c for c in OBLIGATORIOS if c not in encabezados]
    if faltantes:
        raise ErrorPadron(
            "Faltan columnas obligatorias: " + ", ".join(faltantes) + "."
        )
    registros = []
    for numero, fila in enumerate(filas[1:], start=2):
        registro = {"_fila": numero}
        for indice, nombre in enumerate(encabezados):
            if nombre in CAMPOS:
                registro[nombre] = str(fila[indice]).strip() if indice < len(fila) else ""
        registros.append(registro)
    return registros


# ---------------------------------------------------------------------------
# Importación a la base de datos
# ---------------------------------------------------------------------------
def validar_registro(registro):
    """Devuelve (datos_limpios, error, avisos) para una fila del padrón."""
    avisos = []
    numero_socio = (registro.get("numero_socio") or "").strip()
    if numero_socio.endswith(".0") and numero_socio[:-2].isdigit():
        numero_socio = numero_socio[:-2]
    rut = normalizar_rut(registro.get("rut"))
    nombre = (registro.get("nombre") or "").strip()
    apellidos = (registro.get("apellidos") or "").strip()
    if not numero_socio or not re.fullmatch(r"[A-Za-z0-9\-]{1,20}", numero_socio):
        return None, "número de socio vacío o con caracteres no permitidos", avisos
    if not rut:
        return None, f"RUT inválido ({registro.get('rut') or 'vacío'})", avisos
    if not nombre:
        return None, "falta el nombre", avisos

    email = (registro.get("email") or "").strip()
    if email and not PATRON_CORREO.match(email):
        avisos.append(f"correo «{email}» no es válido; se omitió")
        email = ""

    telefono_crudo = (registro.get("telefono") or "").strip()
    telefono = telefono_chile(telefono_crudo)
    if telefono_crudo and not telefono:
        avisos.append(f"teléfono «{telefono_crudo}» no es un móvil chileno; se guardó tal cual")
        telefono = telefono_crudo

    nacimiento = normalizar_fecha(registro.get("fecha_nacimiento"))
    if nacimiento is None:
        avisos.append(f"fecha de nacimiento «{registro.get('fecha_nacimiento')}» no reconocida; se omitió")
        nacimiento = ""
    elif nacimiento and nacimiento > date.today().isoformat():
        avisos.append("fecha de nacimiento futura; se omitió")
        nacimiento = ""

    return {
        "numero_socio": numero_socio, "rut": rut, "nombre": nombre,
        "apellidos": apellidos,
        "direccion": (registro.get("direccion") or "").strip(),
        "telefono": telefono, "email": email, "fecha_nacimiento": nacimiento,
        "fecha_ingreso": normalizar_fecha_ingreso(registro.get("fecha_ingreso")),
        "fallecido": 1 if es_verdadero(registro.get("fallecido")) else 0,
    }, None, avisos


def importar_padron(registros, db, simular=False):
    """Inserta o actualiza socios. ``db`` es una conexión sqlite3 con row_factory.

    Devuelve un diccionario con ``creados``, ``actualizados``, ``errores`` y ``avisos``.
    Todo ocurre en una sola transacción: si algo falla de forma inesperada no queda
    un padrón a medias.
    """
    resultado = {"creados": 0, "actualizados": 0, "errores": [], "avisos": []}
    ahora = datetime.now().isoformat(timespec="seconds")
    vistos_socio, vistos_rut = set(), set()
    try:
        for registro in registros:
            fila = registro.get("_fila", "?")
            datos, error, avisos = validar_registro(registro)
            resultado["avisos"].extend(f"Fila {fila}: {a}" for a in avisos)
            if error:
                resultado["errores"].append(f"Fila {fila}: {error}")
                continue
            if datos["numero_socio"] in vistos_socio or datos["rut"] in vistos_rut:
                resultado["errores"].append(f"Fila {fila}: socio o RUT repetido en el archivo")
                continue
            vistos_socio.add(datos["numero_socio"])
            vistos_rut.add(datos["rut"])

            existente = db.execute(
                "SELECT id, rol FROM usuarios WHERE numero_socio = ? OR rut = ? "
                "ORDER BY (numero_socio = ?) DESC LIMIT 1",
                (datos["numero_socio"], datos["rut"], datos["numero_socio"]),
            ).fetchone()

            if existente:
                if existente["rol"] != "socio":
                    resultado["errores"].append(
                        f"Fila {fila}: el RUT o número ya pertenece a una cuenta con rol "
                        f"«{existente['rol']}»"
                    )
                    continue
                # Lo que el socio ya completó en su perfil (correo, teléfono,
                # nacimiento) no se borra si el padrón trae esas celdas vacías.
                db.execute(
                    "UPDATE usuarios SET nombre = ?, apellidos = ?, rut = ?, numero_socio = ?, "
                    "direccion = ?, telefono = COALESCE(NULLIF(?, ''), telefono), "
                    "email = COALESCE(NULLIF(?, ''), email), "
                    "fecha_nacimiento = COALESCE(NULLIF(?, ''), fecha_nacimiento), "
                    "fecha_ingreso = COALESCE(NULLIF(?, ''), fecha_ingreso), "
                    "fallecido = ?, activo = CASE WHEN ? = 1 THEN 0 ELSE activo END "
                    "WHERE id = ?",
                    (
                        datos["nombre"], datos["apellidos"], datos["rut"], datos["numero_socio"],
                        datos["direccion"], datos["telefono"], datos["email"],
                        datos["fecha_nacimiento"], datos["fecha_ingreso"],
                        datos["fallecido"], datos["fallecido"], existente["id"],
                    ),
                )
                resultado["actualizados"] += 1
                continue

            usuario = usuario_desde_rut(datos["rut"])
            if db.execute("SELECT 1 FROM usuarios WHERE usuario = ?", (usuario,)).fetchone():
                usuario = f"socio{datos['numero_socio'].lower()}"
                if db.execute("SELECT 1 FROM usuarios WHERE usuario = ?", (usuario,)).fetchone():
                    resultado["errores"].append(f"Fila {fila}: no se pudo generar un usuario único")
                    continue
            db.execute(
                "INSERT INTO usuarios (nombre, usuario, email, clave_hash, rol, activo, "
                "estado_aprobacion, fecha_creacion, rut, numero_socio, apellidos, direccion, "
                "telefono, fecha_nacimiento, fecha_ingreso, fallecido, debe_cambiar_clave) "
                "VALUES (?, ?, ?, ?, 'socio', ?, 'Aprobada', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    datos["nombre"], usuario, datos["email"],
                    # En simulación no vale la pena cifrar cientos de claves.
                    "simulacion" if simular else generate_password_hash(clave_inicial(datos["rut"])),
                    0 if datos["fallecido"] else 1, ahora, datos["rut"],
                    datos["numero_socio"], datos["apellidos"], datos["direccion"],
                    datos["telefono"], datos["fecha_nacimiento"], datos["fecha_ingreso"],
                    datos["fallecido"],
                ),
            )
            resultado["creados"] += 1
        if simular:
            db.rollback()
        else:
            db.commit()
    except Exception:
        db.rollback()
        raise
    return resultado


def main(argumentos):
    simular = "--simular" in argumentos
    rutas = [a for a in argumentos if not a.startswith("--")]
    if len(rutas) != 1:
        print(__doc__)
        return 2
    ruta = rutas[0]
    try:
        with open(ruta, "rb") as archivo:
            registros = leer_padron(archivo.read(), ruta)
    except (OSError, ErrorPadron) as error:
        print(f"No se pudo leer el padrón: {error}")
        return 1

    import entorno

    entorno.cargar()
    import app as modulo  # importa la app y deja la base de datos lista

    with modulo.app.app_context():
        resultado = importar_padron(registros, modulo.base.get_db(), simular=simular)
    print(("SIMULACIÓN · " if simular else "") + (
        f"{resultado['creados']} socios creados, {resultado['actualizados']} actualizados."
    ))
    for linea in resultado["avisos"]:
        print("  aviso:", linea)
    for linea in resultado["errores"]:
        print("  error:", linea)
    return 0 if not resultado["errores"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
