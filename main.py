from fastapi import FastAPI, Depends, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from pydantic import BaseModel
import jwt
from datetime import datetime, timedelta

from database import SessionLocal, engine
import models

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="WMS Enterprise API - Sistema InmoFormwork")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- MODELOS PYDANTIC ---
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

class ReparacionCreate(BaseModel):
    id_producto: str
    cantidad: int

class ProductoCreate(BaseModel):
    id_producto: str
    nombre: str
    costo_reposicion: float
    cantidad_inicial: int = 0 

class ObraCreate(BaseModel):
    ruc: str = None
    nombre_proyecto: str
    ubicacion: str = None
    encargado: str = None
    telefono: str = None

# --- SEGURIDAD JWT ---
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
        raise HTTPException(status_code=401, detail="Sesión expirada")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Credencial inválida")
    
    usuario = db.query(models.Usuario).filter(models.Usuario.username == username).first()
    if not usuario:
        raise HTTPException(status_code=401, detail="Usuario no existe")
    return usuario

# --- RUTAS DE LA API ---

@app.post("/login/")
def iniciar_sesion(req: LoginRequest, db: Session = Depends(get_db)):
    usuario = db.query(models.Usuario).filter(models.Usuario.username == req.username).first()
    if not usuario or usuario.password_hash != req.password:
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos")
    
    expiracion = datetime.utcnow() + timedelta(hours=8)
    token_data = {"sub": usuario.username, "rol": usuario.rol, "exp": expiracion}
    token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
    
    return {"access_token": token, "rol": usuario.rol, "token_type": "bearer"}

@app.get("/dashboard/inventario/")
def ver_estado_inventario(response: Response, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    inventario = db.query(models.InventarioLotes).all()
    resultado = []
    for item in inventario:
        producto = db.query(models.CatalogoProducto).filter_by(id_producto=item.id_producto).first()
        resultado.append({
            "id_producto": item.id_producto,
            "nombre": producto.nombre if producto else "Desconocido",
            "cantidad_almacen": item.cantidad_almacen,
            "cantidad_en_obra": item.cantidad_en_obra,
            "cantidad_mantenimiento": item.cantidad_mantenimiento
        })
    return resultado

@app.get("/kardex/")
def obtener_kardex(response: Response, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    kardex = db.query(models.GuiaMovimiento).order_by(models.GuiaMovimiento.fecha_hora.desc()).all()
    resultado = []
    for mov in kardex:
        resultado.append({
            "tipo": mov.tipo_movimiento,
            "fecha": mov.fecha_hora,
            "id_obra": mov.id_obra or "Almacén",
            "id_producto": mov.id_producto,
            "cantidad": mov.cantidad,
            "detalle": f"Registrado por {usuario_activo.username}"
        })
    return resultado

@app.post("/despachos/")
def registrar_despacho(movimiento: MovimientoCreate, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    try:
        nuevo_movimiento = models.GuiaMovimiento(id_obra=movimiento.id_obra, id_producto=movimiento.id_producto, tipo_movimiento="SALIDA", cantidad=movimiento.cantidad)
        db.add(nuevo_movimiento)
        inventario = db.query(models.InventarioLotes).filter_by(id_producto=movimiento.id_producto).first()
        if inventario and inventario.cantidad_almacen >= movimiento.cantidad:
            inventario.cantidad_almacen -= movimiento.cantidad
            inventario.cantidad_en_obra += movimiento.cantidad
        else:
            raise HTTPException(status_code=400, detail="Stock insuficiente en almacén")
        db.commit()
        return {"mensaje": "Despacho registrado"}
    except HTTPException: raise
    except Exception as e: db.rollback(); raise HTTPException(status_code=400, detail=str(e))

@app.post("/devoluciones/")
def registrar_devolucion(devolucion: DevolucionCreate, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    try:
        total_devuelto = devolucion.cantidad_buena + devolucion.cantidad_mantenimiento + devolucion.cantidad_perdida
        if total_devuelto == 0: raise HTTPException(status_code=400, detail="Cantidad debe ser mayor a 0")
        
        nuevo_movimiento = models.GuiaMovimiento(id_obra=devolucion.id_obra, id_producto=devolucion.id_producto, tipo_movimiento="DEVOLUCIÓN", cantidad=total_devuelto)
        db.add(nuevo_movimiento)
        
        if devolucion.cantidad_perdida > 0:
            perdida = models.GuiaMovimiento(id_obra=devolucion.id_obra, id_producto=devolucion.id_producto, tipo_movimiento="PÉRDIDA", cantidad=devolucion.cantidad_perdida)
            db.add(perdida)
            
        inventario = db.query(models.InventarioLotes).filter_by(id_producto=devolucion.id_producto).first()
        if inventario:
            inventario.cantidad_en_obra -= total_devuelto
            inventario.cantidad_almacen += devolucion.cantidad_buena
            inventario.cantidad_mantenimiento += devolucion.cantidad_mantenimiento
            if inventario.cantidad_en_obra < 0: inventario.cantidad_en_obra = 0 
        else: raise HTTPException(status_code=404, detail="Producto no encontrado")
        db.commit()
        return {"mensaje": "Devolución registrada"}
    except HTTPException: raise
    except Exception as e: db.rollback(); raise HTTPException(status_code=400, detail=str(e))

@app.post("/productos/")
def registrar_producto(req: ProductoCreate, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    if db.query(models.CatalogoProducto).filter_by(id_producto=req.id_producto).first():
        raise HTTPException(status_code=400, detail="El código de producto ya existe")
    
    nuevo_prod = models.CatalogoProducto(id_producto=req.id_producto, nombre=req.nombre, tipo_rastreo="LOTE", costo_reposicion=req.costo_reposicion, peso_kg=0.0)
    db.add(nuevo_prod)
    
    nuevo_lote = models.InventarioLotes(id_producto=req.id_producto, cantidad_almacen=req.cantidad_inicial, cantidad_en_obra=0, cantidad_mantenimiento=0)
    db.add(nuevo_lote)
    db.commit()
    return {"mensaje": "Producto registrado exitosamente"}

@app.post("/reparaciones/")
def retornar_mantenimiento(req: ReparacionCreate, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    inventario = db.query(models.InventarioLotes).filter_by(id_producto=req.id_producto).first()
    if not inventario or inventario.cantidad_mantenimiento < req.cantidad:
        raise HTTPException(status_code=400, detail="No hay suficientes equipos en mantenimiento para retornar")
    
    inventario.cantidad_mantenimiento -= req.cantidad
    inventario.cantidad_almacen += req.cantidad
    
    movimiento_reparacion = models.GuiaMovimiento(id_obra=None, id_producto=req.id_producto, tipo_movimiento="REPARACIÓN", cantidad=req.cantidad)
    db.add(movimiento_reparacion)
    db.commit()
    return {"mensaje": "Equipos retornados al almacén principal"}

@app.get("/clientes/estado/")
def estado_clientes(response: Response, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    movimientos = db.query(models.GuiaMovimiento).all()
    obras = {}
    for m in movimientos:
        if m.id_obra is None: continue
        if m.id_obra not in obras: obras[m.id_obra] = {}
        if m.id_producto not in obras[m.id_obra]: obras[m.id_obra][m.id_producto] = 0
            
        if m.tipo_movimiento == "SALIDA": obras[m.id_obra][m.id_producto] += m.cantidad
        elif m.tipo_movimiento in ["DEVOLUCIÓN", "PÉRDIDA"]: obras[m.id_obra][m.id_producto] -= m.cantidad
            
    resultado = []
    for obra_id, prods in obras.items():
        activos = []
        for p_id, cant in prods.items():
            if cant > 0:
                prod = db.query(models.CatalogoProducto).filter_by(id_producto=p_id).first()
                activos.append({"codigo": p_id, "nombre": prod.nombre if prod else "Desconocido", "cantidad": cant})
        if activos: resultado.append({"id_obra": obra_id, "equipos": activos})
    return resultado

@app.get("/obras/{id_obra}/liquidacion/")
def generar_liquidacion(id_obra: int, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    perdidas = db.query(models.GuiaMovimiento).filter(models.GuiaMovimiento.id_obra == id_obra, models.GuiaMovimiento.tipo_movimiento == "PÉRDIDA").all()
    total_penalidad = 0
    desglose = []
    for p in perdidas:
        producto = db.query(models.CatalogoProducto).filter(models.CatalogoProducto.id_producto == p.id_producto).first()
        precio = producto.costo_reposicion if producto else 0
        subtotal = p.cantidad * precio
        total_penalidad += subtotal
        desglose.append({"producto": p.id_producto, "cantidad_perdida": p.cantidad, "costo_unitario": precio, "subtotal_cobro": subtotal})
    return {"id_obra": id_obra, "penalidad_total_moneda": float(total_penalidad), "desglose_perdidas": desglose}

# --- NUEVOS ENDPOINTS: OBRAS ---
@app.post("/obras/")
def registrar_obra(obra: ObraCreate, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    try:
        nueva_obra = models.ObrasClientes(
            id_cliente=obra.ruc if obra.ruc else "CLIENTE-NUEVO", # <- El dato que nos faltaba
            ruc=obra.ruc, 
            nombre_proyecto=obra.nombre_proyecto, 
            ubicacion=obra.ubicacion, 
            encargado=obra.encargado, 
            telefono=obra.telefono, 
            estado_obra="Activa"
        )
        db.add(nueva_obra)
        db.commit()
        db.refresh(nueva_obra)
        return {"mensaje": "Obra registrada con éxito", "id_obra": nueva_obra.id_obra}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="Error en BD: " + str(e))

@app.get("/obras/")
def listar_obras(response: Response, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return db.query(models.ObrasClientes).all()

# Endpoint para Borrar Obras
@app.delete("/obras/{id_obra}")
def eliminar_obra(id_obra: int, db: Session = Depends(get_db), usuario_activo = Depends(get_usuario_actual)):
    obra = db.query(models.ObrasClientes).filter(models.ObrasClientes.id_obra == id_obra).first()
    if not obra:
        raise HTTPException(status_code=404, detail="Obra no encontrada")
    
    # Bloqueo de seguridad: No borrar si ya tiene equipos prestados o historial
    tiene_movimientos = db.query(models.GuiaMovimiento).filter(models.GuiaMovimiento.id_obra == id_obra).first()
    if tiene_movimientos:
        raise HTTPException(status_code=400, detail="No puedes borrar una obra que tiene historial de despachos.")
        
    db.delete(obra)
    db.commit()
    return {"mensaje": "Obra eliminada con éxito"}