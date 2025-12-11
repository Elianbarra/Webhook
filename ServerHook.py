import os
import time
import unicodedata
import random
import requests
import re
from datetime import datetime, date, timedelta
from flask import Flask, request, jsonify

app = Flask(__name__)

# ===================== SESIONES EN MEMORIA =====================

sessions = {}


def get_visitor_id(payload: dict) -> str:
    """Obtiene un identificador estable del visitante."""
    visitor = payload.get("visitor") or {}
    return str(
        visitor.get("id")
        or visitor.get("visitor_id")
        or visitor.get("email")
        or visitor.get("phone")
        or visitor.get("ip")
        or "anon"
    )


def build_reply(texts, input_card=None, action="reply") -> dict:
    """Crea la estructura mínima de respuesta que Zobot entiende."""
    if isinstance(texts, str):
        replies = [texts]
    else:
        replies = list(texts)

    response = {
        "action": action,
        "replies": replies
    }

    if input_card is not None:
        response["input"] = input_card

    return response


def normalizar_texto(txt: str) -> str:
    """Normaliza texto (minúsculas y sin acentos) para comparar opciones."""
    if not txt:
        return ""
    txt = txt.lower()
    txt = "".join(
        c for c in unicodedata.normalize("NFD", txt)
        if unicodedata.category(c) != "Mn"
    )
    return txt.strip()


# ===================== INTEGRACIÓN ZOHO CRM =====================

CRM_BASE = "https://www.zohoapis.com/crm/v2.1"
ACCOUNTS_BASE = "https://accounts.zoho.com"

access_token_cache = {
    "token": None,
    "expires_at": 0.0,  # timestamp UNIX
}


def get_access_token() -> str:
    """
    Devuelve un access token válido usando refresh_token si es necesario.
    Usa las variables de entorno:
      - ZOHO_CLIENT_ID
      - ZOHO_CLIENT_SECRET
      - ZOHO_REFRESH_TOKEN
    """
    now = time.time()
    if (
        access_token_cache["token"]
        and access_token_cache["expires_at"] - 60 > now
    ):
        return access_token_cache["token"]

    client_id = os.environ.get("ZOHO_CLIENT_ID")
    client_secret = os.environ.get("ZOHO_CLIENT_SECRET")
    refresh_token = os.environ.get("ZOHO_REFRESH_TOKEN")

    if not client_id or not client_secret or not refresh_token:
        print("ERROR: faltan ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN.")
        return None

    url = f"{ACCOUNTS_BASE}/oauth/v2/token"
    params = {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
    }

    try:
        resp = requests.post(url, params=params, timeout=10)
        print("=== Respuesta refresh token Zoho ===")
        print(resp.status_code, resp.text)

        if resp.status_code != 200:
            return None

        data = resp.json()
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))

        if not token:
            print("ERROR: respuesta sin access_token.")
            return None

        access_token_cache["token"] = token
        access_token_cache["expires_at"] = time.time() + expires_in
        return token

    except Exception as e:
        print("ERROR llamando a Zoho Accounts:", e)
        return None


def obtener_o_crear_account(campos: dict):
    """
    Busca un Account por Billing_Code (RUT).
    Si existe, devuelve su ID.
    Si no existe, crea uno nuevo con:
      - Account_Name = empresa
      - Billing_Code = rut
      - Phone       = telefono
      - Cliente_Selec = "NO"
      - Owner       = (María Rengifo o Joaquín Gonzalez, al azar)
    """
    access_token = get_access_token()
    if not access_token:
        print("No se pudo obtener access token; se omite Accounts.")
        return None

    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    rut = (campos.get("rut") or "").strip()
    empresa = (campos.get("empresa") or "").strip()
    telefono = (campos.get("telefono") or "").strip()

    if not rut and not empresa:
        return None

    owners_posibles = [
        {"name": "Maria Rengifo",    "id": "4358923000003278018"},
        {"name": "Joaquin Gonzalez", "id": "4358923000011940001"},
    ]
    owner_elegido = random.choice(owners_posibles)
    print(f"Owner elegido para Account: {owner_elegido['name']} ({owner_elegido['id']})")

    if rut:
        try:
            criteria = f"(Billing_Code:equals:{rut})"
            search_url = f"{CRM_BASE}/Accounts/search"
            params = {"criteria": criteria}
            resp = requests.get(search_url, headers=headers, params=params, timeout=10)
            print("=== Búsqueda Account por Billing_Code ===")
            print(resp.status_code, resp.text)

            if resp.status_code == 200:
                data = resp.json()
                registros = data.get("data") or []
                if registros:
                    account_id = registros[0].get("id")
                    if account_id:
                        return account_id
        except Exception as e:
            print("ERROR buscando Account:", e)

    account_name = empresa or rut or "Sin nombre"
    account_data = {
        "Account_Name": account_name,
        "Billing_Code": rut or None,
        "Phone": telefono or None,
        "Cliente_Selec": "NO",
        "Industry": "Por definir",
        "Region1": "Por definir",
        "Ciudad_I": "Por definir",
        "Website": "Por definir",
        "Owner": {"id": owner_elegido["id"]},
    }

    create_url = f"{CRM_BASE}/Accounts"
    payload = {"data": [account_data]}

    try:
        resp = requests.post(create_url, headers=headers, json=payload, timeout=10)
        print("=== Creación Account ===")
        print(resp.status_code, resp.text)

        if resp.status_code in (200, 201):
            data = resp.json()
            registros = data.get("data") or []
            if registros:
                details = registros[0].get("details") or {}
                account_id = details.get("id")
                return account_id
    except Exception as e:
        print("ERROR creando Account:", e)

    return None


def calcular_closing_date(fecha_base: date) -> str:
    """
    Replica la lógica Deluge usando fecha_base como fecha_limite_oferta:
    - Si día < 15   => último día del mismo mes
    - Si día >= 15  => último día del mes siguiente
    Devuelve string en formato YYYY-MM-DD para Zoho (Closing_Date).
    """
    dia = fecha_base.day
    mes = fecha_base.month
    anio = fecha_base.year

    target_mes = mes
    target_anio = anio

    if dia >= 15:
        if mes == 12:
            target_mes = 1
            target_anio = anio + 1
        else:
            target_mes = mes + 1

    if target_mes in (4, 6, 9, 11):
        ultimo_dia = 30
    elif target_mes == 2:
        es_bisiesto = (target_anio % 400 == 0) or (target_anio % 4 == 0 and target_anio % 100 != 0)
        ultimo_dia = 29 if es_bisiesto else 28
    else:
        ultimo_dia = 31

    fecha_cierre = date(target_anio, target_mes, ultimo_dia)
    return fecha_cierre.strftime("%Y-%m-%d")


def crear_deal_en_zoho(campos: dict, account_id: str = None):
    """
    Crea un Deal en Zoho CRM usando los datos del formulario del bot.
    'campos' viene de manejar_flujo_cotizacion_bloque.
    Si viene account_id, lo vincula al campo Account_Name del Deal.
    Asigna Owner aleatoriamente entre María Rengifo y Joaquín Gonzalez.
    """
    access_token = get_access_token()
    if not access_token:
        print("No se pudo obtener access token de Zoho; se omite creación de Deal.")
        return None

    ahora = datetime.now().astimezone()
    manana = ahora + timedelta(days=1)
    fecha_hora_1_str = manana.isoformat(timespec="seconds")
    fecha_limite_oferta = manana.date()
    closing_date_str = calcular_closing_date(fecha_limite_oferta)

    url = f"{CRM_BASE}/Deals"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    owners_posibles = [
        {"nombre": "Maria Rengifo",   "id": "4358923000003278018"},
        {"nombre": "Joaquin Gonzalez","id": "4358923000011940001"}
    ]
    owner_elegido = random.choice(owners_posibles)
    print(f"Owner elegido para el Deal: {owner_elegido['nombre']} ({owner_elegido['id']})")

    deal_data = {
        "Deal_Name": f"Cotización - {campos.get('empresa') or 'Sin empresa'}",
        "Description": (
            "Solicitud recibida desde SalesIQ Webhook.\n\n"
            f"Empresa: {campos.get('empresa')}\n"
            f"Giro: {campos.get('giro')}\n"
            f"RUT: {campos.get('rut')}\n"
            f"Contacto: {campos.get('contacto')}\n"
            f"Correo: {campos.get('correo')}\n"
            f"Teléfono: {campos.get('telefono')}\n"
            f"Producto / descripción: {campos.get('num_parte')}\n"
            f"Marca: {campos.get('marca')}\n"
            f"Cantidad: {campos.get('cantidad')}\n"
            f"Dirección de entrega: {campos.get('direccion_entrega')}"
        ),
        "Stage": "Pendiente por cotizar",
        "Lead_Source": "Chat Whatsapp",
        "Amount": "1",
        "Owner": {"id": owner_elegido["id"]},
        "Asignado_a": {"id": owner_elegido["id"]},
        "Type": "Industrias",
        "Fecha_hora_1": fecha_hora_1_str,
        "Closing_Date": closing_date_str,
    }

    if account_id:
        deal_data["Account_Name"] = account_id

    payload = {"data": [deal_data]}

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        print("=== Respuesta Zoho CRM (Deals) ===")
        print(resp.status_code, resp.text)
        return resp
    except Exception as e:
        print("ERROR llamando a Zoho CRM:", e)
        return None


# ===================== ENDPOINT WEBHOOK SALESIQ =====================

@app.route("/", methods=["GET"])
def index():
    return "Webhook server running"


@app.route("/salesiq-webhook", methods=["GET", "POST"])
def salesiq_webhook():
    if request.method == "GET":
        return jsonify({"status": "ok", "message": "Use POST desde Zoho SalesIQ"})

    payload = request.get_json(force=True, silent=True) or {}
    handler = payload.get("handler")
    operation = payload.get("operation")
    visitor_id = get_visitor_id(payload)

    session = sessions.setdefault(visitor_id, {
        "state": "inicio",
        "data": {}
    })

    print("=== SalesIQ payload ===")
    print(payload)

    if handler == "trigger":
        session["state"] = "menu_principal"
        respuesta = build_reply(
            [
                "¡Bienvenido! Gracias por contactar con Selec.",
                "Por favor, seleccione una de las siguientes opciones para atender su solicitud."
            ],
            input_card={
                "type": "select",
                "options": [
                    "Solicitud Cotización",
                    "Servicio PostVenta"
                ]
            }
        )
        return jsonify(respuesta)

    if handler == "message":
        message_text = extraer_mensaje(payload)
        print("=== mensaje extraído ===", repr(message_text))
        state = session.get("state", "inicio")

        if state in ("menu_principal", "inicio"):
            return jsonify(manejar_menu_principal(session, message_text))

        if state == "cotizacion_bloque":
            return jsonify(manejar_flujo_cotizacion_bloque(session, message_text))

        if state == "postventa_bloque":
            return jsonify(manejar_flujo_postventa_bloque(session, message_text))

        session["state"] = "menu_principal"
        respuesta = build_reply(
            [
                "No he comprendido su mensaje.",
                "Por favor, indique si desea 'Solicitud Cotización' o 'Servicio PostVenta'."
            ]
        )
        return jsonify(respuesta)

    return jsonify(build_reply("He recibido su mensaje."))


def extraer_mensaje(payload: dict) -> str:
    """
    Extrae el texto del mensaje desde el JSON de SalesIQ.
    Intenta primero en payload['message'], luego en payload['request']['message'].
    """
    msg_obj = payload.get("message")
    if not msg_obj:
        req_obj = payload.get("request") or {}
        msg_obj = req_obj.get("message")

    if isinstance(msg_obj, dict):
        txt = msg_obj.get("text") or msg_obj.get("value") or ""
        return str(txt).strip()

    if isinstance(msg_obj, str):
        return msg_obj.strip()

    return ""


def manejar_menu_principal(session: dict, message_text: str) -> dict:
    texto_norm = normalizar_texto(message_text)

    # Solicitud Cotización
    if (
        "cotiz" in texto_norm
        or "solicitud cotizacion" in texto_norm
        or texto_norm == "cotizacion"
    ):
        session["state"] = "cotizacion_bloque"
        formulario = (
            "Perfecto, trabajaremos en su solicitud de cotización.\n"
            "Por favor responda copiando y completando este formulario en un solo mensaje:\n\n"
            "Nombre de la empresa:\n"
            "Giro:\n"
            "RUT:\n"
            "Nombre de contacto:\n"
            "Correo:\n"
            "Teléfono:\n"
            "Número de parte o descripción detallada:\n"
            "Marca:\n"
            "Cantidad:\n"
            "Dirección de entrega:"
        )
        return build_reply(formulario)

    # Servicio PostVenta en un solo bloque
    if (
        "postventa" in texto_norm
        or "post venta" in texto_norm
        or "servicio postventa" in texto_norm
    ):
        session["state"] = "postventa_bloque"
        formulario = (
            "Perfecto, trabajaremos en su solicitud de postventa.\n"
            "Por favor responda copiando y completando este formulario en un solo mensaje:\n\n"
            "Nombre:\n"
            "RUT:\n"
            "Número de factura:\n"
            "Descripción del problema:"
        )
        return build_reply(formulario)

    # Cualquier otra cosa -> derivar a operador humano
    session["state"] = "derivado_operador"
    return {
        "action": "forward",
        "replies": [
            "En este momento no puedo gestionar esta solicitud automáticamente.",
            "Le voy a derivar con un ejecutivo para que le ayude."
        ]
    }


def manejar_flujo_cotizacion_bloque(session: dict, message_text: str) -> dict:
    """
    Acumula la información en session['data'].
    El usuario puede enviar primero el formulario casi completo y luego,
    en mensajes posteriores, solo los datos que falten o correcciones.
    """
    data = session["data"]
    texto = message_text or ""
    lineas = [l for l in texto.splitlines() if l.strip()]

    # Punto de partida: lo ya guardado en la sesión
    campos = {
        "empresa": data.get("empresa", ""),
        "giro": data.get("giro", ""),
        "rut": data.get("rut", ""),
        "contacto": data.get("contacto", ""),
        "correo": data.get("correo", ""),
        "telefono": data.get("telefono", ""),
        "num_parte": data.get("num_parte", ""),
        "cantidad": data.get("cantidad", ""),
        "marca": data.get("marca", ""),
        "direccion_entrega": data.get("direccion_entrega", "")
    }

    lineas_sin_label = []

    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue

        if ":" in linea:
            etiqueta, valor = linea.split(":", 1)
            etiqueta_norm = normalizar_texto(etiqueta)
            valor_clean = valor.strip()
            if not valor_clean:
                # Si el usuario manda "Correo:" sin valor, no borramos lo ya guardado
                continue

            if (
                "empresa" in etiqueta_norm
                or "razon social" in etiqueta_norm
                or "razon_social" in etiqueta_norm
            ):
                campos["empresa"] = valor_clean

            elif "giro" in etiqueta_norm or "actividad" in etiqueta_norm:
                campos["giro"] = valor_clean

            elif etiqueta_norm in ("rut", "r.u.t", "r u t"):
                campos["rut"] = valor_clean

            elif "contacto" in etiqueta_norm:
                campos["contacto"] = valor_clean

            elif "correo" in etiqueta_norm or "email" in etiqueta_norm:
                campos["correo"] = valor_clean

            elif "telefono" in etiqueta_norm or "teléfono" in etiqueta_norm:
                campos["telefono"] = valor_clean

            elif (
                "numero de parte" in etiqueta_norm
                or "numero parte" in etiqueta_norm
                or "descripcion" in etiqueta_norm
                or "descripción" in etiqueta_norm
            ):
                campos["num_parte"] = valor_clean

            elif "marca" in etiqueta_norm:
                campos["marca"] = valor_clean

            elif (
                "direccion de entrega" in etiqueta_norm
                or "dirección de entrega" in etiqueta_norm
                or "direccion" in etiqueta_norm
                or "dirección" in etiqueta_norm
                or "domicilio" in etiqueta_norm
            ):
                campos["direccion_entrega"] = valor_clean

            elif "cantidad" in etiqueta_norm:
                campos["cantidad"] = valor_clean

            else:
                lineas_sin_label.append(linea)

        else:
            linea_norm = normalizar_texto(linea)

            if not campos["correo"]:
                m_mail = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", linea)
                if m_mail:
                    campos["correo"] = m_mail.group(0)
                    continue

            if not campos["rut"]:
                m_rut = re.search(r"\d{1,3}\.?\d{3}\.?\d{3}-[\dkK]", linea)
                if m_rut:
                    campos["rut"] = m_rut.group(0)
                    continue

                if "rut" in linea_norm:
                    partes = linea.split()
                    if len(partes) >= 2:
                        campos["rut"] = partes[-1].strip()
                        continue

            if not campos["telefono"]:
                solo_digitos = re.sub(r"\D", "", linea)
                if 7 <= len(solo_digitos) <= 12 and "@" not in linea:
                    campos["telefono"] = solo_digitos
                    continue

            if not campos["empresa"] and (
                "nombre de la empresa" in linea_norm
                or "razon social" in linea_norm
            ):
                valor_emp = linea
                for clave in ["nombre de la empresa", "razon social"]:
                    idx = linea_norm.find(clave)
                    if idx != -1:
                        offset = idx + len(clave)
                        valor_emp = linea[offset:].strip(" :.-")
                        break
                campos["empresa"] = valor_emp or linea
                continue

            if not campos["giro"] and ("giro" in linea_norm or "actividad" in linea_norm):
                valor_giro = linea
                for clave in ["giro", "actividad"]:
                    idx = linea_norm.find(clave)
                    if idx != -1:
                        offset = idx + len(clave)
                        valor_giro = linea[offset:].strip(" :.-")
                        break
                campos["giro"] = valor_giro or linea
                continue

            if not campos["direccion_entrega"] and (
                "direccion" in linea_norm
                or "dirección" in linea_norm
                or "domicilio" in linea_norm
            ):
                valor_dir = linea
                for clave in ["direccion", "dirección", "domicilio"]:
                    idx = linea_norm.find(clave)
                    if idx != -1:
                        offset = idx + len(clave)
                        valor_dir = linea[offset:].strip(" :.-")
                        break
                campos["direccion_entrega"] = valor_dir or linea
                continue

            if not campos["contacto"] and "contacto" in linea_norm:
                partes = linea.split("contacto", 1)
                if len(partes) > 1:
                    campos["contacto"] = partes[1].strip(" :.-")
                else:
                    campos["contacto"] = linea
                continue

            lineas_sin_label.append(linea)

    # Fallback empresa
    if not campos["empresa"]:
        for l in list(lineas_sin_label):
            ln = normalizar_texto(l)
            if "@" in l:
                continue
            if "rut" in ln:
                continue
            solo_digitos = re.sub(r"\D", "", l)
            if 7 <= len(solo_digitos) <= 12:
                continue
            campos["empresa"] = l
            lineas_sin_label.remove(l)
            break

    # Fallback giro
    if not campos["giro"]:
        for l in list(lineas_sin_label):
            ln = normalizar_texto(l)
            if "giro" in ln or "actividad" in ln:
                campos["giro"] = l
                lineas_sin_label.remove(l)
                break
        if not campos["giro"] and lineas_sin_label:
            campos["giro"] = lineas_sin_label.pop(0)

    # Fallback num_parte
    if not campos["num_parte"] and lineas_sin_label:
        campos["num_parte"] = " ".join(lineas_sin_label)

    # Fallback cantidad si sigue vacía: último número del texto
    if not str(campos["cantidad"]).strip():
        numeros = re.findall(r"\b\d+(?:[.,]\d+)?\b", texto)
        if numeros:
            campos["cantidad"] = numeros[-1].replace(",", ".")

    # Actualizar el estado acumulado de la sesión
    data.update(campos)

    obligatorios = [
        "empresa",
        "giro",
        "rut",
        "contacto",
        "correo",
        "telefono",
        "num_parte",
        "cantidad",
    ]

    nombres_legibles = {
        "empresa": "Nombre de la empresa",
        "giro": "Giro",
        "rut": "RUT",
        "contacto": "Nombre de contacto",
        "correo": "Correo",
        "telefono": "Teléfono",
        "num_parte": "Número de parte o descripción detallada",
        "cantidad": "Cantidad",
    }

    faltantes = [
        nombres_legibles[campo]
        for campo in obligatorios
        if not str(data.get(campo, "")).strip()
    ]

    try:
        cantidad_val = float(str(data.get("cantidad", "")).replace(",", "."))
        if cantidad_val <= 0:
            if "Cantidad (debe ser mayor a 0)" not in faltantes:
                faltantes.append("Cantidad (debe ser mayor a 0)")
    except Exception:
        if "Cantidad (valor numérico)" not in faltantes:
            faltantes.append("Cantidad (valor numérico)")

    if faltantes:
        session["state"] = "cotizacion_bloque"
        mensaje_error = (
            "Hay datos obligatorios que faltan o son inválidos, por lo que no "
            "hemos podido registrar su solicitud.\n\n"
            "Campos a corregir:\n- " + "\n- ".join(faltantes) + "\n\n"
            "No es necesario que vuelva a copiar todo el formulario; "
            "en su próximo mensaje puede enviar únicamente los datos faltantes, "
            "por ejemplo:\n"
            "Correo: cliente@empresa.com\n"
            "Cantidad: 5"
        )
        return {
            "action": "reply",
            "replies": [mensaje_error]
        }

    resumen = (
        "Resumen de su solicitud de cotización:\n"
        f"Nombre de la empresa: {data['empresa']}\n"
        f"Giro: {data['giro']}\n"
        f"RUT: {data['rut']}\n"
        f"Nombre de contacto: {data['contacto']}\n"
        f"Correo: {data['correo']}\n"
        f"Teléfono: {data['telefono']}\n"
        f"Número de parte / descripción: {data['num_parte']}\n"
        f"Cantidad: {data['cantidad']}\n"
        f"Marca: {data['marca']}\n"
        f"Dirección de entrega: {data['direccion_entrega']}"
    )

    account_id = obtener_o_crear_account(data)
    crear_deal_en_zoho(data, account_id=account_id)

    session["state"] = "menu_principal"

    return {
        "action": "reply",
        "replies": [
            "Gracias. Hemos registrado su solicitud con el siguiente detalle:",
            resumen,
            "Un ejecutivo de Selec se pondrá en contacto con usted."
        ]
    }


def manejar_flujo_postventa_bloque(session: dict, message_text: str) -> dict:
    """
    Postventa en un solo mensaje, pero también acumula campos en session['data']
    para permitir correcciones parciales.
    """
    data = session["data"]
    texto = message_text or ""
    lineas = texto.splitlines()

    campos = {
        "nombre": data.get("nombre", ""),
        "rut": data.get("rut", ""),
        "numero_factura": data.get("numero_factura", ""),
        "detalle": data.get("detalle", ""),
    }

    for linea in lineas:
        if ":" not in linea:
            linea_plana = linea.strip()
            if linea_plana:
                if campos["detalle"]:
                    campos["detalle"] += " " + linea_plana
                else:
                    campos["detalle"] = linea_plana
            continue

        etiqueta, valor = linea.split(":", 1)
        etiqueta_norm = normalizar_texto(etiqueta)
        valor_clean = valor.strip()
        if not valor_clean:
            continue

        if "nombre" in etiqueta_norm:
            campos["nombre"] = valor_clean
        elif etiqueta_norm in ("rut", "r.u.t", "r u t"):
            campos["rut"] = valor_clean
        elif "factura" in etiqueta_norm or "n° factura" in etiqueta_norm:
            campos["numero_factura"] = valor_clean
        elif "descripcion" in etiqueta_norm or "descripción" in etiqueta_norm or "problema" in etiqueta_norm:
            campos["detalle"] = valor_clean

    data.update(campos)

    obligatorios = ["nombre", "rut", "numero_factura"]
    nombres_legibles = {
        "nombre": "Nombre",
        "rut": "RUT",
        "numero_factura": "Número de factura",
    }

    faltantes = [
        nombres_legibles[campo]
        for campo in obligatorios
        if not str(data.get(campo, "")).strip()
    ]

    if faltantes:
        session["state"] = "postventa_bloque"
        mensaje_error = (
            "Hay datos obligatorios que faltan o son inválidos, por lo que no "
            "hemos podido registrar correctamente su solicitud de postventa.\n\n"
            "Campos a corregir:\n- " + "\n- ".join(faltantes) + "\n\n"
            "No es necesario que vuelva a copiar todo el formulario; "
            "en su próximo mensaje puede enviar únicamente los datos faltantes, "
            "por ejemplo:\n"
            "Nombre: Juan Pérez\n"
            "Número de factura: 12345"
        )
        return {
            "action": "reply",
            "replies": [mensaje_error]
        }

    resumen = (
        "Resumen de su solicitud de postventa:\n"
        f"Nombre: {data['nombre']}\n"
        f"RUT: {data['rut']}\n"
        f"Número de factura: {data['numero_factura']}\n"
        f"Descripción del problema: {data['detalle'] or '(sin detalle adicional)'}"
    )

    session["state"] = "menu_principal"

    return {
        "action": "reply",
        "replies": [
            "Gracias. Hemos registrado su solicitud de postventa con el siguiente detalle:",
            resumen,
            "En unos momentos un operador de Selec revisará su caso."
        ]
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=True)
