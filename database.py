from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Formato de conexión a PostgreSQL con tu contraseña real
URL_BASE_DATOS = "postgresql://postgres:jeffry16022004@localhost:5432/inventario_encofrado"

engine = create_engine(URL_BASE_DATOS)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependencia para conectar la base de datos con las rutas
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()