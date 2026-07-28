from pydantic import BaseModel

class ProductoBase(BaseModel):
    id_producto: str
    nombre: str
    tipo_rastreo: str
    costo_reposicion: float
    peso_kg: float

    class Config:
        from_attributes = True

class InventarioBase(BaseModel):
    id_producto: str
    cantidad_almacen: int

    class Config:
        from_attributes = True

class ObraBase(BaseModel):
    id_cliente: str
    nombre_proyecto: str
    estado_obra: str = "Activa"

    class Config:
        from_attributes = True

class MovimientoBase(BaseModel):
    tipo_movimiento: str 
    id_obra: int
    id_producto: str
    cantidad: int

    class Config:
        from_attributes = True

class DevolucionBase(BaseModel):
    id_obra: int
    id_producto: str
    cantidad_buena: int
    cantidad_mantenimiento: int
    cantidad_perdida: int

    class Config:
        from_attributes = True