"""
Modelos SQLAlchemy — mismo modelo de datos que el prototipo (ver el `SCHEMA`
de app/db.py en firma-poderes), portado de SQL crudo a ORM para poder
correr sobre SQLite en desarrollo y PostgreSQL en producción sin cambiar
código (solo `DATABASE_URL`).
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Participante(Base):
    __tablename__ = "participantes"

    id = Column(String, primary_key=True)
    token = Column(String, unique=True, nullable=False, index=True)
    nombre_completo = Column(String)
    tipo_documento = Column(String, default="CC")
    numero_documento = Column(String)
    email = Column(String)
    celular = Column(String)
    estado = Column(String, default="invitado")
    foto_cedula_path = Column(String)
    selfie_path = Column(String)
    resultado_verificacion = Column(Text)
    device_os = Column(String)
    creado_en = Column(String)
    actualizado_en = Column(String)

    comparendos = relationship("Comparendo", back_populates="participante")
    documentos = relationship("DocumentoFirmado", back_populates="participante")
    eventos = relationship("EventoAuditoria", back_populates="participante")
    otps = relationship("OtpChallenge", back_populates="participante")
    remisiones = relationship("Remision", back_populates="participante")


class Comparendo(Base):
    __tablename__ = "comparendos"

    id = Column(String, primary_key=True)
    participante_id = Column(String, ForeignKey("participantes.id"), nullable=False)
    numero_comparendo = Column(String)
    fecha_comparendo = Column(String)  # texto YYYY-MM-DD, por simplicidad
    placa = Column(String)
    tipo_vehiculo = Column(String)
    secretaria_transito_id = Column(Integer, ForeignKey("secretarias_transito.id"))  # cuál de las 37 secretarías impartió el comparendo
    titular_nombre = Column(String)  # puede diferir del participante (vehículo de un tercero/empresa)
    titular_documento = Column(String)
    creado_en = Column(String)

    participante = relationship("Participante", back_populates="comparendos")


class DocumentoFirmado(Base):
    __tablename__ = "documentos_firmados"

    id = Column(String, primary_key=True)
    participante_id = Column(String, ForeignKey("participantes.id"), nullable=False)
    tipo = Column(String, nullable=False)
    contenido_render_path = Column(String)
    contenido_final_path = Column(String)
    hash_sha256 = Column(String)
    firmado_en = Column(String)

    participante = relationship("Participante", back_populates="documentos")


class EventoAuditoria(Base):
    __tablename__ = "eventos_auditoria"

    id = Column(String, primary_key=True)
    participante_id = Column(String, ForeignKey("participantes.id"), nullable=False)
    tipo_evento = Column(String, nullable=False)
    canal = Column(String)
    detalle = Column(Text)
    timestamp = Column(String)

    participante = relationship("Participante", back_populates="eventos")


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"

    id = Column(String, primary_key=True)
    participante_id = Column(String, ForeignKey("participantes.id"), nullable=False)
    canal = Column(String)
    codigo_hash = Column(String)
    expira_en = Column(String)
    intentos = Column(Integer, default=0)
    verificado = Column(String)

    participante = relationship("Participante", back_populates="otps")


class Remision(Base):
    """Cascada de remisión certificada (opción 1 -> 3 -> 2). Cada fila es UN
    intento en UN canal; una misma persona puede tener varias filas (una por
    canal probado) hasta que uno quede 'confirmado'."""

    __tablename__ = "remisiones"

    id = Column(String, primary_key=True)
    participante_id = Column(String, ForeignKey("participantes.id"), nullable=False)
    orden = Column(Integer, nullable=False)  # 1=whatsapp_boton, 2=email_confirmacion, 3=share_android
    canal = Column(String, nullable=False)
    estado = Column(String, nullable=False, default="enviado")  # enviado|confirmado|fallido|no_disponible
    proveedor_ref = Column(String)  # id de mensaje / message-id del proveedor
    detalle = Column(Text)  # metadata cruda del proveedor (json)
    constancia_path = Column(String)  # evidencia generada automáticamente
    nivel_evidencia = Column(String)  # 'certificada_proveedor' | 'autorreportada'
    creado_en = Column(String)
    confirmado_en = Column(String)

    participante = relationship("Participante", back_populates="remisiones")


class SecretariaTransito(Base):
    """Catálogo de organismos de tránsito (las 37 secretarías que operaron
    el SAST). Se siembra una sola vez desde app/data/secretarias_transito.py
    (ver seed_secretarias_transito); después de la siembra, este es el lugar
    para CORREGIR un correo si la investigación inicial resultó desactualizada
    o incorrecta, sin tener que tocar código ni redesplegar."""

    __tablename__ = "secretarias_transito"

    id = Column(Integer, primary_key=True, autoincrement=False)
    nombre = Column(String, nullable=False)
    correo_notificacion = Column(String)
    correo_alternativo = Column(String)
    confianza = Column(String)  # alta | media | baja | no_encontrado
    requiere_verificacion_manual = Column(Boolean, default=False)
    fuente = Column(String)
    nota = Column(String)


class DerechoPeticion(Base):
    """Derecho(s) de petición generados y remitidos automáticamente a cada
    secretaría de tránsito involucrada (uno por participante x secretaría,
    agrupando todos los comparendos que esa secretaría le impuso)."""

    __tablename__ = "derechos_peticion"

    id = Column(String, primary_key=True)
    participante_id = Column(String, ForeignKey("participantes.id"), nullable=False)
    secretaria_transito_id = Column(Integer, ForeignKey("secretarias_transito.id"), nullable=False)
    comparendo_ids = Column(Text)  # json: lista de ids de comparendos incluidos
    pdf_path = Column(String)
    hash_sha256 = Column(String)
    email_destino = Column(String)
    estado_envio = Column(String, default="pendiente")  # pendiente|enviado|fallido|bloqueado_verificacion|en_revision_manual
    proveedor_ref = Column(String)  # message-id devuelto por el SMTP del abogado
    creado_en = Column(String)
    enviado_en = Column(String)


class ConfigKV(Base):
    """Interruptor operativo simple (circuit breaker): si la tasa de fallos
    de envío de derechos de petición se dispara, o el equipo jurídico
    necesita pausar manualmente, esta bandera detiene el envío automático
    sin tocar código. Ver app/services/peticion.py."""

    __tablename__ = "config_kv"

    clave = Column(String, primary_key=True)
    valor = Column(String)
