# ServerHook.py
# Webhook para Zobot (Zoho SalesIQ) + creación de Deals en Zoho CRM

import os
import time
import unicodedata

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# ===================== SESIONES EN MEMORIA =====================

# Sesiones en memoria: {visitor_id: {"state": "...", "data": {...}}}
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

CRM_BASE = "https://www.zohoapis.com/crm/v2.1"      # región .com
ACCOUNTS_BASE = "https://accounts.zoho.com"

# Cache en memoria del access token
access_token_cache = {
    "token": None,
    "expires_at": 0.0,   # timestamp UNIX
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
        # Token aún válido (dejamos 60s de margen)
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


def crear_deal_en_zoho(campos: dict):
    """
    Crea un Deal en Zoho CRM usando los datos del formulario del bot.
    'campos' viene de manejar_flujo_cotizacion_bloque.
    """
    access_token = get_access_token()
    if not access_token:
        print("No se pudo obtener access token de Zoho; se omite creación de Deal.")
        return None

    url = f"{CRM_BASE}/Deals"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }

    # Ajuste los API Name según su módulo Deals
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
        "Stage": "Qualification",           # use un Stage existente en su CRM
        "Lead_Source": "SalesIQ Webhook",   # opcional
        # Ejemplo de campo personalizado:
        # "Canal_de_Entrada": "WhatsApp",
    }

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
    # GET solo para pruebas rápidas en el navegador
    if request.method == "GET":
        return jsonify({"status": "ok", "message": "Use POST desde Zoho SalesIQ"})

    payload = request.get_json(force=True, silent=True) or {}
    handler = payload.get("handler")          # "trigger", "message", "context", etc.
    operation = payload.get("operation")      # "chat", "message"... (puede venir vacío)
    visitor_id = get_visitor_id(payload)

    # Recuperar o crear sesión
    session = sessions.setdefault(visitor_id, {
        "state": "inicio",
        "data": {}
    })

    print("=== SalesIQ payload ===")
    print(payload)

    # 1) Primera entrada (trigger)
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

    # 2) Mensajes del usuario
    if handler == "message":
        message_text = extraer_mensaje(payload)
        print("=== mensaje extraído ===", repr(message_text))
        state = session.get("state", "inicio")

        # Menú principal (o inicio)
        if state in ("menu_principal", "inicio"):
            return jsonify(manejar_menu_principal(session, message_text))

        # Flujo de solicitud de cotización (un solo bloque)
        if state == "cotizacion_bloque":
            return jsonify(manejar_flujo_cotizacion_bloque(session, message_text))

        # Flujo de postventa
        if state.startswith("postventa_"):
            return jsonify(manejar_flujo_postventa(session, message_text))

        # Fallback genérico
        session["state"] = "menu_principal"
        respuesta = build_reply(
            [
                "No he comprendido su mensaje.",
                "Por favor, indique si desea 'Solicitud Cotización' o 'Servicio PostVenta'."
            ]
        )
        return jsonify(respuesta)

    # 3) Otros handlers (context, etc.)
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

    # Coincidencias amplias para "Solicitud Cotización"
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

    # "Servicio PostVenta"
    if (
        "postventa" in texto_norm
        or "post venta" in texto_norm
        or "servicio postventa" in texto_norm
    ):
        session["state"] = "postventa_nombre"
        return build_reply(
            [
                "Perfecto, trabajaremos en su solicitud de postventa.",
                "Por favor, indique su nombre:"
            ]
        )

    # Si no reconoce la opción, volvemos a mostrar el menú
    return build_reply(
        [
            "No he podido identificar la opción.",
            "Seleccione una de las siguientes opciones:"
        ],
        input_card={
            "type": "select",
            "options": [
                "Solicitud Cotización",
                "Servicio PostVenta"
            ]
        }
    )


def manejar_flujo_cotizacion_bloque(session: dict, message_text: str) -> dict:
    """
    Recibe un solo mensaje con el formulario completo, lo parsea línea por línea
    y llena session['data'] con los campos. Luego crea el Deal en Zoho CRM.
    """
    data = session["data"]
    texto = message_text or ""
    lineas = texto.splitlines()

    campos = {
        "empresa": "",
        "giro": "",
        "rut": "",
        "contacto": "",
        "correo": "",
        "telefono": "",
        "num_parte": "",
        "marca": "",
        "cantidad": "",
        "direccion_entrega": ""
    }

    for linea in lineas:
        if ":" not in linea:
            continue
        etiqueta, valor = linea.split(":", 1)
        etiqueta_norm = normalizar_texto(etiqueta)
        valor = valor.strip()

        if "empresa" in etiqueta_norm:
            campos["empresa"] = valor
        elif "giro" in etiqueta_norm:
            campos["giro"] = valor
        elif etiqueta_norm in ("rut", "r.u.t", "r u t"):
            campos["rut"] = valor
        elif "contacto" in etiqueta_norm:
            campos["contacto"] = valor
        elif "correo" in etiqueta_norm or "email" in etiqueta_norm:
            campos["correo"] = valor
        elif "telefono" in etiqueta_norm or "telefono" in etiqueta_norm:
            campos["telefono"] = valor
        elif ("numero de parte" in etiqueta_norm
              or "numero de parte" in etiqueta_norm
              or "descripcion" in etiqueta_norm):
            campos["num_parte"] = valor
        elif "marca" in etiqueta_norm:
            campos["marca"] = valor
        elif "cantidad" in etiqueta_norm:
            campos["cantidad"] = valor
        elif "direccion de entrega" in etiqueta_norm:
            campos["direccion_entrega"] = valor

    data.update(campos)

    resumen = (
        "Resumen de su solicitud de cotización:\n"
        f"Nombre de la empresa: {campos['empresa']}\n"
        f"Giro: {campos['giro']}\n"
        f"RUT: {campos['rut']}\n"
        f"Nombre de contacto: {campos['contacto']}\n"
        f"Correo: {campos['correo']}\n"
        f"Teléfono: {campos['telefono']}\n"
        f"Número de parte / descripción: {campos['num_parte']}\n"
        f"Marca: {campos['marca']}\n"
        f"Cantidad: {campos['cantidad']}\n"
        f"Dirección de entrega: {campos['direccion_entrega']}"
    )

    # Crear Deal en Zoho CRM
    crear_deal_en_zoho(campos)

    session["state"] = "menu_principal"

    return {
        "action": "reply",
        "replies": [
            "Gracias. Hemos registrado su solicitud con el siguiente detalle:",
            resumen,
            "Un ejecutivo de Selec se pondrá en contacto con usted."
        ]
    }


def manejar_flujo_postventa(session: dict, message_text: str) -> dict:
    data = session["data"]
    state = session["state"]

    if state == "postventa_nombre":
        data["nombre"] = message_text
        session["state"] = "postventa_rut"
        return build_reply("Indique su RUT:")

    if state == "postventa_rut":
        data["rut"] = message_text
        session["state"] = "postventa_numero_factura"
        return build_reply("Indique el número de factura (si lo tiene):")

    if state == "postventa_numero_factura":
        data["numero_factura"] = message_text
        session["state"] = "postventa_detalle"
        return build_reply("Describa brevemente el problema o solicitud de postventa:")

    if state == "postventa_detalle":
        data["detalle"] = message_text

        resumen = (
            f"Resumen de su solicitud de postventa:\n"
            f"Nombre: {data.get('nombre')}\n"
            f"RUT: {data.get('rut')}\n"
            f"Número de factura: {data.get('numero_factura')}\n"
            f"Detalle: {data.get('detalle')}"
        )

        session["state"] = "menu_principal"

        return {
            "action": "reply",
            "replies": [
                "Gracias. Hemos registrado su solicitud de postventa con el siguiente detalle:",
                resumen,
                "Un ejecutivo se pondrá en contacto con usted."
            ]
        }

    session["state"] = "menu_principal"
    return build_reply(
        [
            "Ha ocurrido un problema con la conversación.",
            "Volvamos al inicio. ¿Desea 'Solicitud Cotización' o 'Servicio PostVenta'?"
        ]
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=True)
