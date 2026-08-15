import os
import psycopg2
import psycopg2.extras
from datetime import datetime
from zoneinfo import ZoneInfo

# La hora "local" del negocio no depende de en qué servidor/región corra
# la nube — la calculamos siempre en esta zona horaria explícita, en vez
# de confiar en la hora del sistema operativo del servidor (que en la
# nube casi siempre es UTC).
ZONA_HORARIA_NEGOCIO = ZoneInfo("America/Mexico_City")


def ahora_local():
    """Fecha/hora actual en la zona horaria del negocio, como string listo para guardar."""
    return datetime.now(ZONA_HORARIA_NEGOCIO).strftime("%Y-%m-%d %H:%M:%S")


class ConnWrapper:
    """Envoltura sobre una conexión de psycopg2 para poder seguir escribiendo
    conn.execute(...).fetchall() igual que con sqlite3, sin tener que tocar
    cada consulta del proyecto una por una."""

    def __init__(self, pg_conn):
        self._conn = pg_conn

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        cur.execute(sql, params if params is not None else ())
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def cursor(self):
        return self._conn.cursor()


def get_connection():
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError(
            "No se encontró la variable de entorno DATABASE_URL. "
            "BateriasPro 2.0 necesita una base de datos PostgreSQL — "
            "configúrala en tu plataforma de despliegue (Railway, etc.) "
            "o en tu archivo .env si la estás corriendo localmente."
        )

    # Railway (y varias plataformas) a veces entregan la URL con el
    # prefijo viejo "postgres://" en vez de "postgresql://", que
    # psycopg2 ya no acepta directamente.
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    pg_conn = psycopg2.connect(database_url, cursor_factory=psycopg2.extras.RealDictCursor)
    return ConnWrapper(pg_conn)


def crear_tabla():
    conn = get_connection()
    cursor = conn.cursor()

    # ---------- usuarios ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            usuario TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1
        )
    """)

    cursor.execute("SELECT COUNT(*) AS n FROM usuarios")
    if cursor.fetchone()["n"] == 0:
        from werkzeug.security import generate_password_hash
        cursor.execute("""
            INSERT INTO usuarios(usuario, password_hash, activo)
            VALUES (%s, %s, 1)
            ON CONFLICT (usuario) DO NOTHING
        """, ("admin", generate_password_hash("admin123")))

    # ---------- baterías (catálogo) ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS baterias (
            id SERIAL PRIMARY KEY,
            sku TEXT NOT NULL UNIQUE,
            marca TEXT NOT NULL,
            modelo TEXT NOT NULL,
            tipo_vehiculo TEXT NOT NULL DEFAULT 'Auto',
            voltaje REAL NOT NULL DEFAULT 12,
            capacidad_ah REAL,
            cca INTEGER,
            garantia_meses INTEGER NOT NULL DEFAULT 12,
            precio_proveedor REAL NOT NULL DEFAULT 0,
            precio REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Por si vienes de una versión más vieja de la 2.0 sin este campo.
    cursor.execute("ALTER TABLE baterias ADD COLUMN IF NOT EXISTS precio_proveedor REAL NOT NULL DEFAULT 0")

    cursor.execute("SELECT COUNT(*) AS n FROM baterias")
    if cursor.fetchone()["n"] == 0:
        demo = [
            ("LTH-L-22F", "LTH", "L-22F", "Auto", 12, 60, 550, 12, 1280, 1850, 8),
            ("LTH-H-24", "LTH", "H-24", "Auto", 12, 70, 640, 15, 1620, 2350, 5),
            ("ACD-27DC", "ACDelco", "27DC", "Camión", 12, 90, 750, 18, 2350, 3400, 3),
            ("BSH-S4008", "Bosch", "S4 008", "Auto", 12, 74, 680, 24, 2000, 2900, 6),
            ("BSH-M4", "Bosch", "M4 Moto", "Moto", 12, 12, 150, 12, 640, 950, 10),
            ("VRLA-YB", "Yuasa", "YB14-A2", "Moto", 12, 14, 165, 12, 710, 1050, 7),
            ("ACD-31DC", "ACDelco", "31DC", "Camión", 12, 100, 800, 18, 2700, 3900, 2),
            ("LTH-CH-31", "LTH", "CH-31", "Camión", 12, 95, 780, 15, 2480, 3600, 4),
        ]
        cursor.executemany("""
            INSERT INTO baterias
                (sku, marca, modelo, tipo_vehiculo, voltaje, capacidad_ah, cca, garantia_meses, precio_proveedor, precio, stock)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sku) DO NOTHING
        """, demo)

    # ---------- clientes ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL,
            telefono TEXT,
            email TEXT,
            rfc TEXT,
            fecha_registro TIMESTAMP
        )
    """)

    # ---------- promociones ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS promociones (
            id SERIAL PRIMARY KEY,
            codigo TEXT NOT NULL UNIQUE,
            descripcion TEXT,
            tipo TEXT NOT NULL DEFAULT 'porcentaje',
            valor REAL NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1
        )
    """)

    # ---------- ventas ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ventas (
            id SERIAL PRIMARY KEY,
            fecha TIMESTAMP,
            cliente_id INTEGER REFERENCES clientes(id),
            usuario_id INTEGER REFERENCES usuarios(id),
            subtotal REAL NOT NULL,
            codigo_promocion TEXT,
            descuento REAL NOT NULL DEFAULT 0,
            total REAL NOT NULL,
            metodo_pago TEXT,
            efectivo REAL,
            cambio REAL,
            folio_factura TEXT
        )
    """)
    cursor.execute("ALTER TABLE ventas ADD COLUMN IF NOT EXISTS usuario_id INTEGER REFERENCES usuarios(id)")

    # ---------- detalle_venta ----------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS detalle_venta (
            id SERIAL PRIMARY KEY,
            venta_id INTEGER NOT NULL REFERENCES ventas(id),
            bateria_id INTEGER NOT NULL REFERENCES baterias(id),
            cantidad INTEGER NOT NULL,
            precio REAL NOT NULL,
            costo REAL NOT NULL DEFAULT 0,
            subtotal REAL NOT NULL
        )
    """)
    cursor.execute("ALTER TABLE detalle_venta ADD COLUMN IF NOT EXISTS costo REAL NOT NULL DEFAULT 0")

    # ---------- índices ----------
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_detalle_venta_venta ON detalle_venta(venta_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_detalle_venta_bateria ON detalle_venta(bateria_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON ventas(fecha)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ventas_cliente ON ventas(cliente_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ventas_usuario ON ventas(usuario_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_baterias_sku ON baterias(sku)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_clientes_nombre ON clientes(nombre)")

    conn.commit()
    cursor.close()
    conn.close()
