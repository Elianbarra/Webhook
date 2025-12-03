# ServerHook.py
# Webhook básico para Zobot (Zoho SalesIQ) en Python + Flask

import os
import unicodedata
from flask import Flask, request, jsonify

app = Flask(__name__)

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


# Ruta simple para comprobar que el servidor está arriba
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

        # Flujo de postventa (sigue paso a paso)
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
    # 1) Formato estándar: mensaje a nivel raíz
    msg_obj = payload.get("message")

    # 2) Alternativa: dentro de 'request'
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
        # Pasamos a modo "bloque"
        session["state"] = "cotizacion_bloque"

        # Enviamos el "formulario" para que lo rellene en un solo mensaje
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

    # Coincidencias amplias para "Servicio PostVenta"
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
    y llena session["data"] con los campos.
    """
    data = session["data"]
    texto = message_text or ""
    lineas = texto.splitlines()

    # Inicializar campos en blanco (por si faltan)
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
        elif "telefono" in etiqueta_norm or "teléfono" in etiqueta_norm:
            campos["telefono"] = valor
        elif ("numero de parte" in etiqueta_norm or
              "número de parte" in etiqueta_norm or
              "descripcion" in etiqueta_norm):
            campos["num_parte"] = valor
        elif "marca" in etiqueta_norm:
            campos["marca"] = valor
        elif "cantidad" in etiqueta_norm:
            campos["cantidad"] = valor
        elif "direccion de entrega" in etiqueta_norm or "dirección de entrega" in etiqueta_norm:
            campos["direccion_entrega"] = valor

    # Guardar en la sesión
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

    # Aquí puedes añadir validaciones (campos obligatorios, etc.)
    # o enviar los datos a Zoho CRM/Creator.

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
