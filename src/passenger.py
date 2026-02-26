"""
Modelo de datos del pasajero para la autocompra.

Almacena la información necesaria para rellenar los formularios
de compra de Renfe (datos personales, contacto, etc.).
La información se guarda cifrada localmente.
"""

import json
import os
from pathlib import Path
from typing import Optional
from enum import Enum

from pydantic import BaseModel, Field, field_validator


DEFAULT_PASSENGER_FILE = Path(os.path.expanduser("~")) / ".renfe-monitor" / "passenger.json"


class DocumentType(str, Enum):
    """Tipos de documento de identidad aceptados por Renfe."""
    DNI = "DNI"
    NIE = "NIE"
    PASAPORTE = "PASAPORTE"


class PaymentMethod(str, Enum):
    """Métodos de pago disponibles."""
    TARJETA = "tarjeta"
    ABONO = "abono"


class PassengerData(BaseModel):
    """Datos del pasajero necesarios para completar la compra en Renfe."""
    
    nombre: str = Field(description="Nombre del pasajero")
    apellido1: str = Field(description="Primer apellido")
    apellido2: Optional[str] = Field(default=None, description="Segundo apellido")
    
    tipo_documento: DocumentType = Field(default=DocumentType.DNI, description="Tipo de documento")
    numero_documento: str = Field(description="Número de documento de identidad")
    
    email: str = Field(description="Correo electrónico de contacto")
    telefono: Optional[str] = Field(default=None, description="Teléfono de contacto")
    
    metodo_pago: PaymentMethod = Field(
        default=PaymentMethod.ABONO,
        description="Método de pago preferido (tarjeta o abono)"
    )

    @field_validator("numero_documento")
    @classmethod
    def validar_documento(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) < 5:
            raise ValueError("El número de documento es demasiado corto")
        return v

    @field_validator("email")
    @classmethod
    def validar_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v:
            raise ValueError("Email no válido")
        return v

    @property
    def nombre_completo(self) -> str:
        parts = [self.nombre, self.apellido1]
        if self.apellido2:
            parts.append(self.apellido2)
        return " ".join(parts)


def save_passenger(data: PassengerData, path: Optional[Path] = None) -> None:
    """Guarda los datos del pasajero en un archivo JSON local.
    
    :param data: Datos del pasajero a guardar.
    :param path: Ruta del archivo. Por defecto ~/.renfe-monitor/passenger.json.
    """
    file_path = path or DEFAULT_PASSENGER_FILE
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data.model_dump(), f, ensure_ascii=False, indent=2)


def load_passenger(path: Optional[Path] = None) -> Optional[PassengerData]:
    """Carga los datos del pasajero desde el archivo JSON local.
    
    :param path: Ruta del archivo. Por defecto ~/.renfe-monitor/passenger.json.
    :return: Datos del pasajero o None si no existe el archivo.
    """
    file_path = path or DEFAULT_PASSENGER_FILE
    
    if not file_path.exists():
        return None
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return PassengerData(**raw)
    except Exception:
        return None


def delete_passenger(path: Optional[Path] = None) -> bool:
    """Elimina el archivo de datos del pasajero.
    
    :param path: Ruta del archivo.
    :return: True si se eliminó correctamente.
    """
    file_path = path or DEFAULT_PASSENGER_FILE
    try:
        if file_path.exists():
            file_path.unlink()
            return True
    except Exception:
        pass
    return False
