from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
import models
import schemas
from fastapi.staticfiles import StaticFiles
from database import engine, get_db

# Crea las tablas
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="API - Sistema de Encofrados")

# NUEVA LÍNEA: Le dice al servidor que aloje nuestra página web en la ruta /app
app.mount("/app", StaticFiles(directory="frontend", html=True), name="frontend")

@app.get("/")
def read_root():
    return {"mensaje": "Servidor de Inventario Operativo correctamente"}

@app.get("/productos/", response_model=list[schemas.ProductoBase])
def obtener_productos(db: Session = Depends(get_db)):
    return db.query(models.CatalogoProducto).all()

@app.post("/productos/", response_model=schemas.ProductoBase)
def crear_producto(producto: schemas.ProductoBase, db: Session = Depends(get_db)):
    nuevo_producto = models.CatalogoProducto(**producto.dict())
    db.add(nuevo_producto)
    db.commit()             
    db.refresh(nuevo_producto) 
    return nuevo_producto

@app.post("/inventario/", response_model=schemas.InventarioBase)
def registrar_inventario(inventario: schemas.InventarioBase, db: Session = Depends(get_db)):
    nuevo_inventario = models.InventarioLotes(**inventario.dict())
    db.add(nuevo_inventario)
    db.commit()
    db.refresh(nuevo_inventario)
    return nuevo_inventario

@app.post("/obras/", response_model=schemas.ObraBase)
def registrar_obra(obra: schemas.ObraBase, db: Session = Depends(get_db)):
    nueva_obra = models.ObrasClientes(**obra.dict())
    db.add(nueva_obra)
    db.commit()
    db.refresh(nueva_obra)
    return nueva_obra

@app.post("/despachos/", response_model=schemas.MovimientoBase)
def registrar_despacho(movimiento: schemas.MovimientoBase, db: Session = Depends(get_db)):
    # 1. Seguridad: Verificar que sea una "Salida"
    if movimiento.tipo_movimiento != "Salida":
        raise HTTPException(status_code=400, detail="Esta ruta es solo para Salidas")
    
    # 2. Buscar el inventario de ese producto
    inventario = db.query(models.InventarioLotes).filter(models.InventarioLotes.id_producto == movimiento.id_producto).first()
    
    # 3. Seguridad: Verificar si existe el producto y si hay stock suficiente
    if not inventario:
        raise HTTPException(status_code=404, detail="Producto no encontrado en el inventario")
    if inventario.cantidad_almacen < movimiento.cantidad:
        raise HTTPException(status_code=400, detail=f"Stock insuficiente. Solo hay {inventario.cantidad_almacen} disponibles.")

    # 4. Actualizar cantidades
    inventario.cantidad_almacen -= movimiento.cantidad
    inventario.cantidad_en_obra += movimiento.cantidad

    # 5. Registrar en el historial de movimientos
    nuevo_movimiento = models.GuiaMovimiento(
        tipo_movimiento=movimiento.tipo_movimiento,
        id_obra=movimiento.id_obra,
        id_producto=movimiento.id_producto,
        cantidad=movimiento.cantidad
    )
    db.add(nuevo_movimiento)
    db.commit()
    return nuevo_movimiento

@app.post("/devoluciones/")
def registrar_devolucion(devolucion: schemas.DevolucionBase, db: Session = Depends(get_db)):
    total_devuelto = devolucion.cantidad_buena + devolucion.cantidad_mantenimiento + devolucion.cantidad_perdida
    
    # 1. Buscar el inventario de ese producto
    inventario = db.query(models.InventarioLotes).filter(models.InventarioLotes.id_producto == devolucion.id_producto).first()
    
    if not inventario:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
        
    # 2. Seguridad: Verificar que no devuelvan más de lo que llevaron
    if inventario.cantidad_en_obra < total_devuelto:
        raise HTTPException(status_code=400, detail=f"Inconsistencia: La obra solo tiene {inventario.cantidad_en_obra} unidades registradas.")
        
    # 3. Repartir el stock según el estado en el que regresó
    inventario.cantidad_en_obra -= total_devuelto
    inventario.cantidad_almacen += devolucion.cantidad_buena
    inventario.cantidad_mantenimiento += devolucion.cantidad_mantenimiento
    
    # 4. Guardar un registro histórico por cada estado para auditorías
    if devolucion.cantidad_buena > 0:
        db.add(models.GuiaMovimiento(tipo_movimiento="Devolucion_Buena", id_obra=devolucion.id_obra, id_producto=devolucion.id_producto, cantidad=devolucion.cantidad_buena))
    if devolucion.cantidad_mantenimiento > 0:
        db.add(models.GuiaMovimiento(tipo_movimiento="Devolucion_Mantenimiento", id_obra=devolucion.id_obra, id_producto=devolucion.id_producto, cantidad=devolucion.cantidad_mantenimiento))
    if devolucion.cantidad_perdida > 0:
        db.add(models.GuiaMovimiento(tipo_movimiento="Devolucion_Perdida", id_obra=devolucion.id_obra, id_producto=devolucion.id_producto, cantidad=devolucion.cantidad_perdida))
        
    db.commit()
    return {
        "mensaje": "Devolución procesada con éxito", 
        "total_retornado": total_devuelto,
        "pendientes_en_obra": inventario.cantidad_en_obra
    }

# ==========================================
# REPORTES Y DASHBOARD
# ==========================================

@app.get("/dashboard/inventario/")
def ver_estado_inventario(db: Session = Depends(get_db)):
    # Trae todo el inventario actual
    lista_inventario = db.query(models.InventarioLotes).all()
    return lista_inventario

@app.get("/obras/{id_obra}/liquidacion/")
def liquidar_obra(id_obra: int, db: Session = Depends(get_db)):
    # Busca todos los registros de material perdido para esta obra
    movimientos_perdidos = db.query(models.GuiaMovimiento).filter(
        models.GuiaMovimiento.id_obra == id_obra,
        models.GuiaMovimiento.tipo_movimiento == "Devolucion_Perdida"
    ).all()

    total_a_cobrar = 0
    detalles = []

    # Calcula el costo multiplicando las unidades perdidas por el costo de reposición
    for mov in movimientos_perdidos:
        producto = db.query(models.CatalogoProducto).filter(models.CatalogoProducto.id_producto == mov.id_producto).first()
        costo_penalidad = producto.costo_reposicion * mov.cantidad
        total_a_cobrar += costo_penalidad
        
        detalles.append({
            "producto": producto.nombre,
            "cantidad_perdida": mov.cantidad,
            "costo_unitario": producto.costo_reposicion,
            "subtotal_cobro": costo_penalidad
        })

    return {
        "id_obra": id_obra,
        "penalidad_total_moneda": total_a_cobrar,
        "desglose_perdidas": detalles
    }