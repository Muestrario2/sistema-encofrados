from fastapi import FastAPI, Depends, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
import models
import schemas
from fastapi.staticfiles import StaticFiles
from database import engine, get_db
import jwt
from datetime import datetime, timedelta
from pydantic import BaseModel

# Crea las tablas
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="API - Sistema de Encofrados")

# --- CONFIGURACIÓN DE SEGURIDAD JWT ---
SECRET_KEY = "InmoFormwork_Clave_Ultra_Segura_2026"
ALGORITHM = "HS256"

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/login/")
def iniciar_sesion(req: LoginRequest, db: Session = Depends(get_db)):
    # 1. Buscar al usuario en la base de datos
    usuario = db.query(models.Usuario).filter(models.Usuario.username == req.username).first()
    
    # 2. Verificar que exista y que la contraseña coincida 
    # (En producción se usa passlib para desencriptar, aquí comparamos texto por simplicidad del demo)
    if not usuario or usuario.password_hash != req.password:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    
    # 3. Generar el Token JWT que expira en 8 horas
    expiracion = datetime.utcnow() + timedelta(hours=8)
    token_data = {
        "sub": usuario.username,
        "rol": usuario.rol,
        "exp": expiracion
    }
    
    token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
    
    # 4. Devolver el token y el rol al frontend
    return {
        "access_token": token,
        "rol": usuario.rol,
        "token_type": "bearer",
        "mensaje": f"Bienvenido {usuario.username}"
    }

# Configuración de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Aloja la página web en la ruta /app
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
    if movimiento.tipo_movimiento != "Salida":
        raise HTTPException(status_code=400, detail="Esta ruta es solo para Salidas")
    
    inventario = db.query(models.InventarioLotes).filter(models.InventarioLotes.id_producto == movimiento.id_producto).first()
    
    if not inventario:
        raise HTTPException(status_code=404, detail="Producto no encontrado en el inventario")
    if inventario.cantidad_almacen < movimiento.cantidad:
        raise HTTPException(status_code=400, detail=f"Stock insuficiente. Solo hay {inventario.cantidad_almacen} disponibles.")

    inventario.cantidad_almacen -= movimiento.cantidad
    inventario.cantidad_en_obra += movimiento.cantidad

    nuevo_movimiento = models.GuiaMovimiento(
        tipo_movimiento=movimiento.tipo_movimiento,
        id_obra=movimiento.id_obra,
        id_producto=movimiento.id_producto,
        cantidad=movimiento.cantidad
    )
    db.add(nuevo_movimiento)

    # Registrar en el Kardex usando la columna 'fecha'
    try:
        db.execute(
            text("INSERT INTO movimientos_kardex (id_obra, id_producto, tipo, cantidad) VALUES (:obra, :prod, :tipo, :cant)"),
            {
                "obra": movimiento.id_obra,
                "prod": movimiento.id_producto,
                "tipo": "SALIDA",
                "cant": movimiento.cantidad
            }
        )
    except Exception as e:
        print("Error al registrar en Kardex:", e)

    db.commit()
    return nuevo_movimiento

@app.post("/devoluciones/")
def registrar_devolucion(devolucion: schemas.DevolucionBase, db: Session = Depends(get_db)):
    total_devuelto = devolucion.cantidad_buena + devolucion.cantidad_mantenimiento + devolucion.cantidad_perdida
    
    inventario = db.query(models.InventarioLotes).filter(models.InventarioLotes.id_producto == devolucion.id_producto).first()
    
    if not inventario:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
        
    if inventario.cantidad_en_obra < total_devuelto:
        raise HTTPException(status_code=400, detail=f"Inconsistencia: La obra solo tiene {inventario.cantidad_en_obra} unidades registradas.")
        
    inventario.cantidad_en_obra -= total_devuelto
    inventario.cantidad_almacen += devolucion.cantidad_buena
    inventario.cantidad_mantenimiento += devolucion.cantidad_mantenimiento
    
    if devolucion.cantidad_buena > 0:
        db.add(models.GuiaMovimiento(tipo_movimiento="Devolucion_Buena", id_obra=devolucion.id_obra, id_producto=devolucion.id_producto, cantidad=devolucion.cantidad_buena))
    if devolucion.cantidad_mantenimiento > 0:
        db.add(models.GuiaMovimiento(tipo_movimiento="Devolucion_Mantenimiento", id_obra=devolucion.id_obra, id_producto=devolucion.id_producto, cantidad=devolucion.cantidad_mantenimiento))
    if devolucion.cantidad_perdida > 0:
        db.add(models.GuiaMovimiento(tipo_movimiento="Devolucion_Perdida", id_obra=devolucion.id_obra, id_producto=devolucion.id_producto, cantidad=devolucion.cantidad_perdida))
        
    # Registrar la devolución en el Kardex usando la columna 'fecha'
    try:
        db.execute(
            text("INSERT INTO movimientos_kardex (id_obra, id_producto, tipo, cantidad) VALUES (:obra, :prod, :tipo, :cant)"),
            {
                "obra": devolucion.id_obra,
                "prod": devolucion.id_producto,
                "tipo": "DEVOLUCIÓN",
                "cant": total_devuelto
            }
        )
    except Exception as e:
        print("Error al registrar devolución en Kardex:", e)

    db.commit()
    return {
        "mensaje": "Devolución procesada con éxito", 
        "total_retornado": total_devuelto,
        "pendientes_en_obra": inventario.cantidad_en_obra
    }

# ENDPOINT PARA LEER EL KARDEX SIN CACHÉ
@app.get("/kardex/")
def obtener_kardex(response: Response, db: Session = Depends(get_db)):
    # Cabeceras anti-caché para forzar datos en tiempo real
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    
    try:
        movimientos = db.execute(text("SELECT id, fecha, id_obra, id_producto, tipo, cantidad FROM movimientos_kardex ORDER BY fecha DESC LIMIT 50")).fetchall()
        resultado = []
        for m in movimientos:
            resultado.append({
                "id": m[0],
                "fecha": m[1],
                "created_at": m[1],
                "id_obra": m[2],
                "id_producto": m[3],
                "tipo": m[4],
                "detalle": f"Movimiento de {m[4].lower()} registrado",
                "cantidad": m[5]
            })
        return resultado
    except Exception as e:
        print("Error al leer Kardex:", e)
        return []

# ESTADO DE INVENTARIO SIN CACHÉ
@app.get("/dashboard/inventario/")
def ver_estado_inventario(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return db.query(models.InventarioLotes).all()

# REPORTES Y DASHBOARD
@app.get("/dashboard/inventario/")
def ver_estado_inventario(db: Session = Depends(get_db)):
    return db.query(models.InventarioLotes).all()

@app.get("/obras/{id_obra}/liquidacion/")
def liquidar_obra(id_obra: int, db: Session = Depends(get_db)):
    movimientos_perdidos = db.query(models.GuiaMovimiento).filter(
        models.GuiaMovimiento.id_obra == id_obra,
        models.GuiaMovimiento.tipo_movimiento == "Devolucion_Perdida"
    ).all()

    total_a_cobrar = 0
    detalles = []

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