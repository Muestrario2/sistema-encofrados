from sqlalchemy import Column, String, Float, Integer, ForeignKey, DateTime
from datetime import datetime
from database import Base

class CatalogoProducto(Base):
    __tablename__ = "catalogo_productos"
    id_producto = Column(String, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    tipo_rastreo = Column(String, nullable=False) 
    costo_reposicion = Column(Float, nullable=False)
    peso_kg = Column(Float)

class InventarioLotes(Base):
    __tablename__ = "inventario_lotes"
    id = Column(Integer, primary_key=True, index=True)
    id_producto = Column(String, ForeignKey("catalogo_productos.id_producto"))
    cantidad_almacen = Column(Integer, default=0)
    cantidad_en_obra = Column(Integer, default=0)
    cantidad_mantenimiento = Column(Integer, default=0)
    
class ObrasClientes(Base):
    __tablename__ = "obras_clientes"
    id_obra = Column(Integer, primary_key=True, index=True)
    ruc = Column(String, nullable=True)
    nombre_proyecto = Column(String, nullable=False)
    ubicacion = Column(String, nullable=True)
    encargado = Column(String, nullable=True)
    telefono = Column(String, nullable=True)
    estado_obra = Column(String, default="Activa")

class GuiaMovimiento(Base):
    __tablename__ = "guia_movimientos"
    id_movimiento = Column(Integer, primary_key=True, index=True)
    tipo_movimiento = Column(String, nullable=False) # 'Salida' o 'Devolucion'
    id_obra = Column(Integer, ForeignKey("obras_clientes.id_obra"))
    id_producto = Column(String, ForeignKey("catalogo_productos.id_producto"))
    cantidad = Column(Integer, nullable=False)
    fecha_hora = Column(DateTime, default=datetime.utcnow)

class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    rol = Column(String, nullable=False)   