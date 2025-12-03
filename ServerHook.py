# ServerHook.py
# Webhook básico para Zobot (Zoho SalesIQ) en Python + Flask

import os
from flask import Flask, request, jsonify

app = Flask(__name__)

# Sesiones en memoria: {visitor_id: {"state": "...", "data": {...}}}
# En producción lo ideal es usar BD/Redis, etc.
sessions = {}


def get_visitor_id(payload: dict) -> str:
    """
    Intenta obtener un identificador estable del visitante.
    Adáptelo según lo que vea en el JSON real de SalesIQ.
    """
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
    """
    Crea la estructura mínima de respuesta que Zobot entiende.
    texts puede ser string o lista de strings.
    """
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


# Ruta simple para comprobar que el servidor está arriba
@app.route("/", methods=["GET"])
def index():
    return "Webhook server running"


@app.route("/salesiq-webhook", methods=["GET", "POST"])
def salesiq_webhook():
    # GET solo para pruebas rápidas en el navegador
    if request.method == "GET":
        return jsonify({"status": "ok", "message": "Use POST desde Zoho SalesIQ"})

    # Desde aquí hacia abajo es la lógica real del webhook (POST)
    payload = request.get_json(force=True, silent=True) or {}
    handler = payload.get("handler")          # "trigger", "message", "context", etc.
    operation = payload.get("operation")      # "chat", "message"... (puede venir vacío)
    visitor_id = get_visitor_id(payload)

    # Recuperar o crear sesión
    session = sessions.setdefault(visitor_id, {
        "state": "inicio",
        "data": {}
    })

    # Log para debug
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
        state = session.get("state", "inicio")

        # Menú principal
        if state == "menu_principal":
            return jsonify(manejar_menu_principal(session, message_text))

        # Flujo de solicitud de cotización
        if state.startswith("cotizacion_"):
            return jsonify(manejar_flujo_cotizacion(session, message_text))

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

    # 3) Otros handlers (context, etc.) – respuesta simple
    return jsonify(build_reply("He recibido su mensaje."))


def extraer_mensaje(payload: dict) -> str:
    """
    Extrae el texto del mensaje desde el JSON de SalesIQ.
    """
    req_obj = payload.get("request") or {}
    msg_obj = req_obj.get("message") or ""

    if isinstance(msg_obj, dict):
        return (msg_obj.get("text") or "").strip()
    if isinstance(msg_obj, str):
        return msg_obj.strip()

    return ""


def manejar_menu_principal(session: dict, message_text: str) -> dict:
    texto = message_text.lower()

    if "cotiz" in texto:
        session["state"] = "cotizacion_empresa"
        return build_reply(
            [
                "Perfecto, trabajaremos en su solicitud de cotización.",
                "Por favor complete la siguiente información.",
                "Nombre de la empresa:"
            ]
        )

    if "postventa" in texto or "post venta" in texto:
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


def manejar_flujo_cotizacion(session: dict, message_text: str) -> dict:
    """
    Flujo detallado de solicitud de cotización, campo por campo:
    Empresa, Giro, RUT, Contacto, Correo, Teléfono,
    Número de parte/descripcion, Marca, Cantidad, Dirección de entrega.
    """
    data = session["data"]
    state = session["state"]

    # 1) Nombre de la empresa
    if state == "cotizacion_empresa":
        data["empresa"] = message_text
        session["state"] = "cotizacion_giro"
        return build_reply("Giro:")

    # 2) Giro
    if state == "cotizacion_giro":
        data["giro"] = message_text
        session["state"] = "cotizacion_rut"
        return build_reply("RUT:")

    # 3) RUT
    if state == "cotizacion_rut":
        data["rut"] = message_text
        session["state"] = "cotizacion_contacto"
        return build_reply("Nombre de contacto:")

    # 4) Nombre de contacto
    if state == "cotizacion_contacto":
        data["contacto"] = message_text
        session["state"] = "cotizacion_correo"
        return build_reply("Correo:")

    # 5) Correo
    if state == "cotizacion_correo":
        data["correo"] = message_text
        session["state"] = "cotizacion_telefono"
        return build_reply("Teléfono:")

    # 6) Teléfono
    if state == "cotizacion_telefono":
        data["telefono"] = message_text
        session["state"] = "cotizacion_num_parte"
        return build_reply("Número de parte (o descripción detallada):")

    # 7) Número de parte / descripción
    if state == "cotizacion_num_parte":
        data["num_parte"] = message_text
        session["state"] = "cotizacion_marca"
        return build_reply("Marca:")

    # 8) Marca
    if state == "cotizacion_marca":
        data["marca"] = message_text
        session["state"] = "cotizacion_cantidad"
        return build_reply("Cantidad:")

    # 9) Cantidad
    if state == "cotizacion_cantidad":
        data["cantidad"] = message_text
        session["state"] = "cotizacion_direccion"
        return build_reply("Dirección de entrega:")

    # 10) Dirección de entrega
    if state == "cotizacion_direccion":
        data["direccion_entrega"] = message_text

        # Aquí puede invocar Zoho CRM/Creator, enviar correo, etc.
        resumen = (
            "Resumen de su solicitud de cotización:\n"
            f"Nombre de la empresa: {data.get('empresa')}\n"
            f"Giro: {data.get('giro')}\n"
            f"RUT: {data.get('rut')}\n"
            f"Nombre de contacto: {data.get('contacto')}\n"
            f"Correo: {data.get('correo')}\n"
            f"Teléfono: {data.get('telefono')}\n"
            f"Número de parte / descripción: {data.get('num_parte')}\n"
            f"Marca: {data.get('marca')}\n"
            f"Cantidad: {data.get('cantidad')}\n"
            f"Dirección de entrega: {data.get('direccion_entrega')}"
        )

        # Volver al menú principal
        session["state"] = "menu_principal"

        return {
            "action": "reply",
            "replies": [
                "Gracias. Hemos registrado su solicitud con el siguiente detalle:",
                resumen,
                "Un ejecutivo de Selec se pondrá en contacto con usted."
            ]
        }

    # Si el estado no coincide, reiniciamos
    session["state"] = "menu_principal"
    return build_reply(
        [
            "Ha ocurrido un problema con la conversación.",
            "Volvamos al inicio. ¿Desea 'Solicitud Cotización' o 'Servicio PostVenta'?"
        ]
    )


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

        # Aquí igualmente podría crear un ticket en Zoho Desk, CRM, etc.

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
