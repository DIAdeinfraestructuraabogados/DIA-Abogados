"""
Paso 3a: generar documentos para revisión (poder + contrato).
Paso 3b: la persona acepta -> se dispara el OTP (WhatsApp con respaldo en correo).
Paso 4: verificar OTP -> sella la firma en los documentos y dispara el
        paso 5 (opción 1 de la cascada de remisión certificada).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app import db
from app.config import CONFIG_CASO
from app.schemas import AceptarBody, VerificarOtpBody
from app.services import documents, notify, peticion, remision
from app.utils import error, ip_cliente

router = APIRouter(prefix="/api/participantes", tags=["documentos"])

TIPOS_DOCUMENTO = ("poder_especial", "contrato_prestacion_servicios")


def _comparendos_con_secretaria(participante_id: str) -> list[dict]:
    """Enriquece cada comparendo con el NOMBRE de su secretaría (los
    documentos lo necesitan para mostrarlo en el texto; la tabla
    comparendos solo guarda el id)."""
    catalogo = {s["id"]: s["nombre"] for s in db.listar_secretarias_transito()}
    salida = []
    for c in db.comparendos_de(participante_id):
        c = dict(c)
        c["secretaria_nombre"] = catalogo.get(c.get("secretaria_transito_id"))
        salida.append(c)
    return salida


def _contexto(p: dict) -> dict:
    return {**CONFIG_CASO, **p, "fecha": db.now()[:10], "comparendos": _comparendos_con_secretaria(p["id"])}


@router.get("/{token}/documentos")
def generar_documentos(token: str):
    p = db.obtener_participante_por_token(token)
    if not p:
        return error("token inválido", 404)

    contexto = _contexto(p)
    salida = {}
    for tipo in TIPOS_DOCUMENTO:
        info = documents.generar_documento_para_revision(tipo, p["id"], contexto)
        db.crear_documento(p["id"], tipo, info["ruta_pdf"])
        salida[tipo] = info["ruta_pdf"]

    db.actualizar_participante(p["id"], estado="documentos_generados")
    db.registrar_evento(p["id"], "documentos_generados_para_revision")
    return salida


@router.get("/{token}/documentos/{tipo}/pdf")
def descargar_pdf(token: str, tipo: str):
    """Sirve el PDF más reciente de ese tipo para este participante: el
    firmado si ya existe, o el borrador de revisión mientras tanto. Usado
    por el frontend para mostrar/descargar el documento sin exponer rutas
    de archivo absolutas del servidor."""
    p = db.obtener_participante_por_token(token)
    if not p:
        return error("token inválido", 404)
    if tipo not in TIPOS_DOCUMENTO:
        return error("tipo de documento inválido", 404)

    ruta = None
    for doc in db.documentos_de(p["id"]):
        if doc["tipo"] == tipo:
            ruta = doc["contenido_final_path"] or doc["contenido_render_path"]
    if not ruta:
        return error("documento no generado todavía", 404)

    return FileResponse(ruta, media_type="application/pdf", filename=f"{tipo}.pdf")


@router.post("/{token}/aceptar")
def aceptar_y_enviar_otp(token: str, body: AceptarBody, request: Request):
    p = db.obtener_participante_por_token(token)
    if not p:
        return error("token inválido", 404)

    # El poder de este caso es específico a comparendo(s); no se firma
    # sin al menos uno registrado (ver paso 1b).
    if not db.comparendos_de(p["id"]):
        return error("registra al menos un comparendo (paso 1b) antes de firmar", 400)

    db.registrar_evento(
        p["id"], "documento_aceptado_pendiente_otp",
        detalle={"ip": ip_cliente(request), "user_agent": request.headers.get("User-Agent")},
    )

    codigo = notify.generar_codigo_otp()
    canal = body.canal or "whatsapp"

    enviado = False
    if canal == "whatsapp" and p["celular"]:
        enviado = notify.enviar_whatsapp_otp(p["celular"], codigo, p["nombre_completo"] or "")
    if not enviado and p["email"]:
        canal = "email"
        enviado = notify.enviar_email_otp(p["email"], codigo, p["nombre_completo"] or "")

    if not enviado:
        return error("no fue posible enviar el código (falta celular/correo)", 400)

    codigo_hash = notify.hash_codigo(codigo, p["id"])
    db.crear_otp(p["id"], canal, codigo_hash)
    db.actualizar_participante(p["id"], estado="otp_enviado")
    db.registrar_evento(p["id"], "otp_generado", canal=canal, detalle={"ip": ip_cliente(request)})

    return {"ok": True, "canal": canal}


@router.post("/{token}/verificar-otp")
def verificar_otp(token: str, body: VerificarOtpBody, request: Request):
    p = db.obtener_participante_por_token(token)
    if not p:
        return error("token inválido", 404)

    codigo_ingresado = body.codigo

    otp = db.ultimo_otp_vigente(p["id"])
    if not otp:
        return error("no hay un código vigente, solicita uno nuevo", 400)
    if db.now() > otp["expira_en"]:
        return error("el código expiró, solicita uno nuevo", 400)
    if int(otp["intentos"]) >= 5:
        return error("demasiados intentos fallidos", 429)

    if notify.hash_codigo(codigo_ingresado, p["id"]) != otp["codigo_hash"]:
        db.incrementar_intentos_otp(otp["id"])
        db.registrar_evento(p["id"], "otp_incorrecto", canal=otp["canal"], detalle={"ip": ip_cliente(request)})
        return error("código incorrecto", 400)

    db.marcar_otp_verificado(otp["id"])
    evento_firma_id = db.new_id()
    db.registrar_evento(
        p["id"], "firma_confirmada", canal=otp["canal"],
        detalle={"ip": ip_cliente(request), "id_evento_firma": evento_firma_id},
    )

    contexto = _contexto(p)
    resultados = {}
    for tipo in TIPOS_DOCUMENTO:
        sello = documents.sellar_documento_firmado(
            tipo, p["id"], contexto,
            fecha_firma_iso=db.now(),
            canal_verificacion=otp["canal"],
            id_evento_firma=evento_firma_id,
        )
        for doc in db.documentos_de(p["id"]):
            if doc["tipo"] == tipo and doc["contenido_final_path"] is None:
                db.marcar_documento_firmado(doc["id"], sello["ruta_pdf"], sello["hash_sha256"])
        resultados[tipo] = {"hash": sello["hash_sha256"], "pdf": sello["ruta_pdf"]}

    db.actualizar_participante(p["id"], estado="firmado")
    # La firma ya quedó sellada arriba; un fallo o demora del proveedor de
    # correo/WhatsApp al enviar la copia de cortesía NO debe tumbar ni
    # colgar la respuesta de la firma (que ya es válida sin esta copia).
    try:
        notify.enviar_copia_firmada(p["email"], p["celular"], resultados["poder_especial"]["pdf"])
    except Exception:
        db.registrar_evento(p["id"], "envio_copia_firmada_fallido", detalle={"pdf": resultados["poder_especial"]["pdf"]})

    # -----------------------------------------------------------
    # Paso 5, automático: dispara la opción 1 de la cascada de
    # remisión certificada (botón de WhatsApp). No requiere que la
    # persona haga nada más en este momento; el resto de la cascada
    # (opciones 3 y 2) se activa desde los endpoints de remision.py
    # cuando la opción 1 falla o expira sin confirmación.
    # -----------------------------------------------------------
    p_actualizado = db.obtener_participante(p["id"])
    # Igual que la copia de cortesía arriba: un fallo del proveedor en la
    # opción 1 de la cascada (ej. WhatsApp real sin credenciales todavía
    # configuradas, o caído) NO debe tumbar la respuesta de la firma, que
    # ya es válida y quedó guardada antes de este punto. El resto de la
    # cascada (opciones 3 y 2) sigue disponible desde los endpoints de
    # remision.py aunque esta primera opción haya fallado.
    try:
        intento_remision = remision.iniciar_cascada(p_actualizado)
    except Exception:
        db.registrar_evento(p["id"], "remision_opcion1_fallida", canal="whatsapp_boton")
        intento_remision = {"id": None, "estado": "fallido"}

    # -----------------------------------------------------------
    # Disparado por el MISMO evento (firma + poder + OTP verificados):
    # se genera y se intenta enviar automáticamente un derecho de
    # petición por cada secretaría de tránsito involucrada, desde el
    # correo del abogado apoderado. Las salvaguardas de
    # app/services/peticion.py deciden si sale ya, si queda bloqueado
    # por verificación pendiente del correo, o en revisión manual.
    # -----------------------------------------------------------
    derechos_peticion = peticion.generar_y_enviar_derechos_peticion(p_actualizado, CONFIG_CASO)

    return {
        "ok": True,
        "documentos": resultados,
        "remision": {
            "opcion_activa": "whatsapp_boton",
            "remision_id": intento_remision["id"],
            "estado": intento_remision["estado"],
        },
        "derechos_peticion": [
            {"secretaria_transito_id": d["secretaria_transito_id"], "estado_envio": d["estado_envio"]}
            for d in derechos_peticion
        ],
    }


@router.get("/{token}/derechos-peticion")
def derechos_peticion_estado(token: str):
    p = db.obtener_participante_por_token(token)
    if not p:
        return error("token inválido", 404)
    return db.derechos_peticion_de(p["id"])
