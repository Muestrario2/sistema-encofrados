from fastapi import FastAPI, Depends, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from pydantic import BaseModel
import jwt
from datetime import datetime, timedelta

from database import SessionLocal, engine
import models

# Crear tablas en BD
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="WMS Enterprise API - Sistema InmoFormwork")

# CORS para permitir conexión con tu frontend en GitHub Pages
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conexión a Base de Datos
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- MODELOS PYDANTIC (ESQUEMAS DE DATOS) ---
class LoginRequest(BaseModel):
    username: str
    password: str

class MovimientoCreate(BaseModel):
    id_obra: int
    id_producto: str
    cantidad: int

class DevolucionCreate(BaseModel):
    id_obra: int
    id_producto: str
    cantidad_buena: int = 0
    cantidad_mantenimiento: int = 0
    cantidad_perdida: int = 0


# --- CONFIGURACIÓN DE SEGURIDAD JWT (EL GUARDIA) ---
SECRET_KEY = "InmoFormwork_Clave_Ultra_Segura_2026"
ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def get_usuario_actual(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Credencial inválida")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="El tiempo de tu sesión ha expirado")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Credencial corrupta o inválida")
    
    usuario = db.query(models.Usuario).filter(models.Usuario.username == username).first()
    if not usuario:
        raise HTTPException(status_code=401, detail="El usuario ya no existe")
    
    return usuario


# --- RUTAS DE LA API ---

@app.post("/login/")
def iniciar_sesion(req: LoginRequest, db: Session = Depends(get_db)):
    usuario = db.query(models.Usuario).filter(models.Usuario.username == req.username).first()
    if not usuario or usuario.password_hash != req.password:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    
    expiracion = datetime.utcnow() + timedelta(hours=8)
    token_data = {
        "sub": usuario.username,
        "rol": usuario.rol,
        "exp": expiracion
    }
    token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
    
    return {
        "access_token": token,
        "rol": usuario.rol,
        "token_type": "bearer",
        "mensaje": f"Bienvenido {usuario.username}"
    }

@app.get("/dashboard/inventario/")
def ver_estado_inventario(response: Response, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    inventario = db.query(models.InventarioLotes).all()
    return inventario

@app.get("/kardex/")
def obtener_kardex(response: Response, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    try:
        kardex = db.query(models.MovimientosKardex).order_by(models.MovimientosKardex.created_at.desc()).all()
        return kardex
    except Exception as e:
        # Por si el campo created_at no se llama así en tu base de datos
        kardex = db.query(models.MovimientosKardex).order_by(models.MovimientosKardex.id.desc()).all()
        return kardex

@app.post("/despachos/")
def registrar_despacho(movimiento: MovimientoCreate, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    try:
        nuevo_movimiento = models.MovimientosKardex(
            id_obra=movimiento.id_obra,
            id_producto=movimiento.id_producto,
            tipo="SALIDA",
            cantidad=movimiento.cantidad
        )
        db.add(nuevo_movimiento)
        
        inventario = db.query(models.InventarioLotes).filter_by(id_producto=movimiento.id_producto).first()
        if inventario:
            if inventario.cantidad_almacen >= movimiento.cantidad:
                inventario.cantidad_almacen -= movimiento.cantidad
                inventario.cantidad_en_obra += movimiento.cantidad
            else:
                raise HTTPException(status_code=400, detail="No hay suficiente stock en almacén")
        else:
            raise HTTPException(status_code=404, detail="Producto no encontrado en el inventario")
            
        db.commit()
        return {"mensaje": "Despacho registrado correctamente"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/devoluciones/")
def registrar_devolucion(devolucion: DevolucionCreate, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    try:
        total_devuelto = devolucion.cantidad_buena + devolucion.cantidad_mantenimiento + devolucion.cantidad_perdida
        if total_devuelto == 0:
            raise HTTPException(status_code=400, detail="La cantidad total devuelta debe ser mayor a 0")
            
        nuevo_movimiento = models.MovimientosKardex(
            id_obra=devolucion.id_obra,
            id_producto=devolucion.id_producto,
            tipo="DEVOLUCIÓN",
            cantidad=total_devuelto
        )
        db.add(nuevo_movimiento)
        
        if devolucion.cantidad_perdida > 0:
            perdida = models.RegistroPerdidas(
                id_obra=devolucion.id_obra,
                id_producto=devolucion.id_producto,
                cantidad_perdida=devolucion.cantidad_perdida
            )
            db.add(perdida)
            
        inventario = db.query(models.InventarioLotes).filter_by(id_producto=devolucion.id_producto).first()
        if inventario:
            inventario.cantidad_en_obra -= total_devuelto
            inventario.cantidad_almacen += devolucion.cantidad_buena
            inventario.cantidad_mantenimiento += devolucion.cantidad_mantenimiento
            
            if inventario.cantidad_en_obra < 0:
                inventario.cantidad_en_obra = 0 
        else:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
            
        db.commit()
        return {"mensaje": "Devolución registrada correctamente"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/obras/{id_obra}/liquidacion/")
def generar_liquidacion(id_obra: int, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    perdidas = db.query(models.RegistroPerdidas).filter(models.RegistroPerdidas.id_obra == id_obra).all()
    
    total_penalidad = 0
    desglose = []
    
    for p in perdidas:
        producto = db.query(models.CatProductos).filter(models.CatProductos.id == p.id_producto).first()
        precio = producto.precio_reposicion if producto else 0
        subtotal = p.cantidad_perdida * precio
        total_penalidad += subtotal
        
        desglose.append({
            "producto": p.id_producto,
            "cantidad_perdida": p.cantidad_perdida,
            "costo_unitario": precio,
            "subtotal_cobro": subtotal
        })
        
    return {
        "id_obra": id_obra,
        "penalidad_total_moneda": float(total_penalidad),
        "desglose_perdidas": desglose
    }