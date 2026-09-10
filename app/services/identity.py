"""
Servicio de verificación de identidad: OCR de la cédula + comparación facial
contra la selfie ("prueba de vida" simplificada).

Este módulo define la INTERFAZ que debe cumplir cualquier proveedor. Por
ahora se incluye una implementación simulada (MockIdentityProvider) porque
todavía no hay credenciales de un proveedor real.

Para producción, reemplazar por un proveedor real, por ejemplo:
  - Truora            (https://www.truora.com)      - OCR + comparación facial LATAM
  - MetaMap           (https://www.metamap.com)      - KYC LATAM, incluye Colombia
  - Registraduría Nacional (validación de cédula contra el registro civil,
    vía convenio institucional; es la validación más fuerte disponible en Colombia)

La función clave es `verificar_identidad`, que debe devolver un objeto
`ResultadoVerificacion` homogéneo sin importar el proveedor usado, de modo
que el resto del sistema (endpoints, generación de documentos) no dependa
del proveedor concreto.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import hashlib
import time


@dataclass
class ResultadoVerificacion:
    ok: bool
    nombre_ocr: Optional[str] = None
    numero_documento_ocr: Optional[str] = None
    score_similitud_facial: Optional[float] = None  # 0.0 - 1.0
    proveedor: str = "mock"
    detalle: dict = field(default_factory=dict)


class IdentityProvider:
    """Interfaz que debe implementar cualquier proveedor real.

    Recibe los BYTES ya descifrados de las imágenes (no una ruta de
    archivo): en disco, foto_cedula/selfie se guardan cifradas (ver
    app/crypto.py y app/routers/identidad.py), así que cualquier proveedor
    real (Truora/MetaMap, que de todas formas reciben la imagen por HTTP
    como bytes/base64, no por ruta local) debe operar sobre estos bytes.
    """

    def verificar_identidad(
        self,
        foto_cedula_bytes: bytes,
        selfie_bytes: bytes,
        numero_documento_declarado: str,
        nombre_declarado: str,
    ) -> ResultadoVerificacion:
        raise NotImplementedError


class MockIdentityProvider(IdentityProvider):
    """
    Implementación de demostración: no llama a ningún servicio externo.
    Simula una verificación exitosa y calcula un hash de las imágenes como
    evidencia de integridad (para que el flujo de auditoría sea real, aunque
    la verificación biométrica en sí sea simulada).
    """

    def verificar_identidad(
        self,
        foto_cedula_bytes: bytes,
        selfie_bytes: bytes,
        numero_documento_declarado: str,
        nombre_declarado: str,
    ) -> ResultadoVerificacion:
        time.sleep(0.1)  # simula latencia de red hacia un proveedor real

        return ResultadoVerificacion(
            ok=True,
            nombre_ocr=nombre_declarado,
            numero_documento_ocr=numero_documento_declarado,
            score_similitud_facial=0.94,
            proveedor="mock",
            detalle={
                "hash_foto_cedula": hashlib.sha256(foto_cedula_bytes).hexdigest(),
                "hash_selfie": hashlib.sha256(selfie_bytes).hexdigest(),
                "nota": "Verificación simulada. Reemplazar por Truora/MetaMap/Registraduría en producción.",
            },
        )


def get_identity_provider() -> IdentityProvider:
    # Punto único de configuración: aquí se decide qué proveedor usar según
    # variables de entorno (ej. IDENTITY_PROVIDER=truora|metamap|mock).
    return MockIdentityProvider()
