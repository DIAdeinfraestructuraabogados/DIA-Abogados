"""Paso 2: foto cédula + selfie -> verificación de identidad."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile

from app import db
from app.config import STORAGE_DIR
from app.crypto import encrypt_bytes
from app.services import identity
from app.utils import error, ip_cliente

router = APIRouter(prefix="/api/participantes", tags=["identidad"])


@router.post("/{token}/identidad")
async def subir_identidad(
    token: str,
    request: Request,
    foto_cedula: UploadFile | None = File(None),
    selfie: UploadFile | None = File(None),
):
    p = db.obtener_participante_por_token(token)
    if not p:
        return error("token inválido", 404)

    if not foto_cedula or not selfie:
        return error("se requieren 'foto_cedula' y 'selfie'", 400)

    carpeta = STORAGE_DIR / p["id"]
    carpeta.mkdir(parents=True, exist_ok=True)

    # Se cifran en reposo (ver app/crypto.py): un acceso directo al disco o
    # a un backup no debe poder abrir estas imágenes (cédula + selfie son
    # exactamente el tipo de dato que se filtró en el incidente de la
    # Secretaría de Movilidad). El nombre ".enc" deja claro que no son un
    # .jpg abrible directamente.
    ruta_cedula = carpeta / "cedula.enc"
    ruta_selfie = carpeta / "selfie.enc"
    bytes_cedula = await foto_cedula.read()
    bytes_selfie = await selfie.read()
    ruta_cedula.write_bytes(encrypt_bytes(bytes_cedula))
    ruta_selfie.write_bytes(encrypt_bytes(bytes_selfie))

    proveedor = identity.get_identity_provider()
    resultado = proveedor.verificar_identidad(
        bytes_cedula, bytes_selfie,
        numero_documento_declarado=p["numero_documento"],
        nombre_declarado=p["nombre_completo"],
    )

    db.actualizar_participante(
        p["id"],
        foto_cedula_path=str(ruta_cedula),
        selfie_path=str(ruta_selfie),
        resultado_verificacion=json.dumps(resultado.__dict__),
        estado="identidad_verificada" if resultado.ok else "rechazado",
    )
    db.registrar_evento(
        p["id"], "identidad_verificada",
        detalle={"ok": resultado.ok, "score_facial": resultado.score_similitud_facial, "ip": ip_cliente(request)},
    )
    return {"ok": resultado.ok, "score_similitud_facial": resultado.score_similitud_facial}
