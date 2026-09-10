"""
Cifrado en reposo (at rest) para los datos personales más sensibles: los
campos de texto de identidad/contacto en la base de datos, y las fotos de
cédula + selfie guardadas en disco.

Por qué existe este módulo: hasta ahora esos datos se guardaban en texto
plano (columnas de la base de datos legibles directo, fotos .jpg abribles
por cualquiera con acceso al disco/backup). Eso es exactamente lo que se
filtró en el incidente de la Secretaría Distrital de Movilidad que motivó
este cambio (nombre, cédula, celular, correo). Cifrar en reposo no evita
una filtración si alguien compromete la aplicación en caliente (ese acceso
ya ve los datos descifrados), pero sí evita que un volcado de la base de
datos, un backup robado, o acceso directo al disco expongan algo útil.

Se usa cifrado simétrico (Fernet/AES128-CBC + HMAC, de la librería
`cryptography`) con UNA sola clave (`FIELD_ENCRYPTION_KEY`). Ver
`_get_or_create_key()` para de dónde sale esa clave.
"""
from __future__ import annotations

import warnings

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import String, TypeDecorator

from app.config import FIELD_ENCRYPTION_KEY

_fernet = Fernet(FIELD_ENCRYPTION_KEY.encode())


def encrypt_bytes(data: bytes) -> bytes:
    """Cifra bytes crudos (para archivos: foto de cédula, selfie)."""
    return _fernet.encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    """Descifra bytes producidos por `encrypt_bytes`."""
    return _fernet.decrypt(data)


class EncryptedString(TypeDecorator):
    """Tipo de columna SQLAlchemy que cifra/descifra de forma transparente.

    El valor cifrado (base64) se guarda en la base de datos; el resto del
    código (routers, servicios, `_to_dict`) sigue viendo el texto plano
    normal al leer el atributo del modelo — no hace falta tocar nada más
    donde ya se lee/escribe `participante["numero_documento"]`, etc.
    No es un tipo indexable/buscable por igualdad exacta (el texto cifrado
    cambia cada vez que se cifra); por eso solo se usa en campos que hoy no
    se filtran con WHERE (ver comentario en models.py).
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return _fernet.encrypt(str(value).encode()).decode()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return _fernet.decrypt(value.encode()).decode()
        except InvalidToken:
            # Filas viejas de antes de activar el cifrado, o clave rotada:
            # se devuelven tal cual en vez de reventar toda la consulta.
            warnings.warn(
                "EncryptedString: valor no descifrable (¿dato pre-cifrado o "
                "clave FIELD_ENCRYPTION_KEY distinta a la que lo escribió?); "
                "se devuelve el valor crudo guardado en la base de datos."
            )
            return value
