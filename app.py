import os
import json
from functools import wraps

from dotenv import load_dotenv
load_dotenv()  # en local, carga DATABASE_URL/SECRET_KEY desde .env si existe;
                # en Railway no hace nada (ya vienen como variables de entorno reales)

from flask import Flask, render_template, request, redirect, abort, session
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2

from database import get_connection, crear_tabla, ahora_local

app = Flask(__name__)

# La llave de sesión ahora SÍ importa de verdad (antes no había login).
# En producción debe venir de una variable de entorno — nunca fija en
# el código, porque el código de este proyecto puede acabar público.
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esto-en-produccion-" + os.urandom(8).hex())

crear_tabla()

TIPOS_VEHICULO = ["Auto", "Moto", "Camión"]


# ============================================================
# Autenticación
# ============================================================
# BateriasPro nació como un sistema de una sola computadora, sin login.
# Al pasar a la nube con acceso desde cualquier dispositivo, cualquiera
# en internet podría entrar sin esto — por eso en la versión 2.0 el
# login es obligatorio, aunque (a diferencia de PVtienda/MyRestaurant)
# aquí no hay distinción de roles: cualquier usuario que inicie sesión
# tiene acceso completo, solo para llevar registro de quién vendió qué.

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(f"/login?next={request.path}")
        return f(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form["usuario"].strip()
        password = request.form["password"]

        conn = get_connection()
        fila = conn.execute(
            "SELECT * FROM usuarios WHERE usuario = %s AND activo = 1", (usuario,)
        ).fetchone()
        conn.close()

        if fila is None or not check_password_hash(fila["password_hash"], password):
            return render_template("login.html", error="Usuario o contraseña incorrectos.")

        session["usuario_id"] = fila["id"]
        session["usuario"] = fila["usuario"]

        siguiente = request.args.get("next") or "/"
        return redirect(siguiente)

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/usuarios")
@login_required
def usuarios():
    conn = get_connection()
    lista = conn.execute("SELECT * FROM usuarios ORDER BY usuario").fetchall()
    conn.close()
    return render_template("usuarios.html", usuarios=lista)


@app.route("/usuarios/agregar", methods=["POST"])
@login_required
def usuarios_agregar():
    usuario = request.form["usuario"].strip()
    password = request.form["password"]

    conn = get_connection()
    error = None
    try:
        conn.execute("""
            INSERT INTO usuarios(usuario, password_hash, activo)
            VALUES (%s, %s, 1)
        """, (usuario, generate_password_hash(password)))
        conn.commit()
    except psycopg2.IntegrityError:
        conn.rollback()
        error = f'Ya existe un usuario llamado "{usuario}".'

    if error:
        lista = conn.execute("SELECT * FROM usuarios ORDER BY usuario").fetchall()
        conn.close()
        return render_template("usuarios.html", usuarios=lista, error=error)

    conn.close()
    return redirect("/usuarios")


@app.route("/usuarios/desactivar/<int:id>")
@login_required
def usuarios_desactivar(id):
    conn = get_connection()

    if id == session["usuario_id"]:
        conn.close()
        return redirect("/usuarios")

    activos = conn.execute("SELECT COUNT(*) AS n FROM usuarios WHERE activo = 1").fetchone()["n"]
    if activos <= 1:
        conn.close()
        return redirect("/usuarios")

    conn.execute("UPDATE usuarios SET activo = 0 WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    return redirect("/usuarios")


@app.route("/usuarios/activar/<int:id>")
@login_required
def usuarios_activar(id):
    conn = get_connection()
    conn.execute("UPDATE usuarios SET activo = 1 WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    return redirect("/usuarios")


# ============================================================
# Inicio — panel con top ventas y alertas de inventario
# ============================================================

@app.route("/")
@login_required
def inicio():
    conn = get_connection()

    hoy_fecha = ahora_local()[:10]  # "YYYY-MM-DD" en la zona horaria del negocio

    hoy = conn.execute("""
        SELECT COALESCE(SUM(total), 0) AS total, COUNT(*) AS cantidad
        FROM ventas WHERE DATE(fecha) = %s
    """, (hoy_fecha,)).fetchone()

    top_ventas = conn.execute("""
        SELECT b.marca, b.modelo, b.tipo_vehiculo,
               SUM(d.cantidad) AS unidades,
               SUM(
                   CASE WHEN v.subtotal > 0
                        THEN d.subtotal * (v.total * 1.0 / v.subtotal)
                        ELSE d.subtotal
                   END
               ) AS ingresos
        FROM detalle_venta d
        JOIN baterias b ON b.id = d.bateria_id
        JOIN ventas v ON v.id = d.venta_id
        GROUP BY b.id
        ORDER BY unidades DESC
        LIMIT 6
    """).fetchall()

    max_unidades = max([f["unidades"] for f in top_ventas], default=0)

    bajo_stock = conn.execute("""
        SELECT * FROM baterias WHERE stock <= 3 ORDER BY stock ASC
    """).fetchall()

    conn.close()

    return render_template(
        "inicio.html",
        total_hoy=hoy["total"],
        cantidad_hoy=hoy["cantidad"],
        top_ventas=top_ventas,
        max_unidades=max_unidades,
        bajo_stock=bajo_stock,
    )


# ============================================================
# Catálogo de baterías — CRUD
# ============================================================

@app.route("/baterias")
@login_required
def baterias():
    filtro_tipo = request.args.get("tipo", "")

    conn = get_connection()
    if filtro_tipo:
        lista = conn.execute("""
            SELECT * FROM baterias WHERE tipo_vehiculo = %s
            ORDER BY marca, modelo
        """, (filtro_tipo,)).fetchall()
    else:
        lista = conn.execute("SELECT * FROM baterias ORDER BY marca, modelo").fetchall()
    conn.close()

    return render_template("baterias.html", baterias=lista, tipos=TIPOS_VEHICULO, filtro_tipo=filtro_tipo)


@app.route("/baterias/agregar", methods=["GET", "POST"])
@login_required
def baterias_agregar():
    if request.method == "POST":
        conn = get_connection()
        error = None
        try:
            conn.execute("""
                INSERT INTO baterias
                    (sku, marca, modelo, tipo_vehiculo, voltaje, capacidad_ah, cca, garantia_meses, precio_proveedor, precio, stock)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                request.form["sku"].strip(),
                request.form["marca"].strip(),
                request.form["modelo"].strip(),
                request.form["tipo_vehiculo"],
                float(request.form["voltaje"] or 12),
                float(request.form["capacidad_ah"]) if request.form.get("capacidad_ah") else None,
                int(request.form["cca"]) if request.form.get("cca") else None,
                int(request.form["garantia_meses"] or 12),
                float(request.form.get("precio_proveedor") or 0),
                float(request.form["precio"]),
                int(request.form["stock"] or 0),
            ))
            conn.commit()
        except psycopg2.IntegrityError:
            conn.rollback()
            error = f'Ya existe una batería con el SKU "{request.form["sku"]}".'

        if error:
            conn.close()
            return render_template("agregar_bateria.html", tipos=TIPOS_VEHICULO, error=error)

        conn.close()
        return redirect("/baterias")

    return render_template("agregar_bateria.html", tipos=TIPOS_VEHICULO)


@app.route("/baterias/editar/<int:id>", methods=["GET", "POST"])
@login_required
def baterias_editar(id):
    conn = get_connection()

    if request.method == "POST":
        conn.execute("""
            UPDATE baterias
            SET sku = %s, marca = %s, modelo = %s, tipo_vehiculo = %s, voltaje = %s,
                capacidad_ah = %s, cca = %s, garantia_meses = %s, precio_proveedor = %s, precio = %s, stock = %s
            WHERE id = %s
        """, (
            request.form["sku"].strip(),
            request.form["marca"].strip(),
            request.form["modelo"].strip(),
            request.form["tipo_vehiculo"],
            float(request.form["voltaje"] or 12),
            float(request.form["capacidad_ah"]) if request.form.get("capacidad_ah") else None,
            int(request.form["cca"]) if request.form.get("cca") else None,
            int(request.form["garantia_meses"] or 12),
            float(request.form.get("precio_proveedor") or 0),
            float(request.form["precio"]),
            int(request.form["stock"] or 0),
            id,
        ))
        conn.commit()
        conn.close()
        return redirect("/baterias")

    bateria = conn.execute("SELECT * FROM baterias WHERE id = %s", (id,)).fetchone()
    conn.close()

    if bateria is None:
        return abort(404)

    return render_template("editar_bateria.html", bateria=bateria, tipos=TIPOS_VEHICULO)


@app.route("/baterias/eliminar/<int:id>")
@login_required
def baterias_eliminar(id):
    conn = get_connection()
    en_uso = conn.execute(
        "SELECT COUNT(*) AS n FROM detalle_venta WHERE bateria_id = %s", (id,)
    ).fetchone()["n"]

    if en_uso > 0:
        # Ya se vendió antes: la dejamos en stock 0 en vez de borrarla,
        # para no perder el historial de ventas que la referencian.
        conn.execute("UPDATE baterias SET stock = 0 WHERE id = %s", (id,))
    else:
        conn.execute("DELETE FROM baterias WHERE id = %s", (id,))

    conn.commit()
    conn.close()
    return redirect("/baterias")


# ============================================================
# Promociones / códigos de descuento — CRUD
# ============================================================

@app.route("/promociones")
@login_required
def promociones():
    conn = get_connection()
    lista = conn.execute("SELECT * FROM promociones ORDER BY activo DESC, codigo").fetchall()
    conn.close()
    return render_template("promociones.html", promociones=lista)


@app.route("/promociones/agregar", methods=["POST"])
@login_required
def promociones_agregar():
    codigo = request.form["codigo"].strip().upper()
    descripcion = request.form["descripcion"].strip()
    tipo = request.form["tipo"]
    valor = float(request.form["valor"])

    conn = get_connection()
    error = None
    try:
        conn.execute("""
            INSERT INTO promociones(codigo, descripcion, tipo, valor, activo)
            VALUES (%s, %s, %s, %s, 1)
        """, (codigo, descripcion, tipo, valor))
        conn.commit()
    except Exception:
        conn.rollback()
        error = f'Ya existe un código "{codigo}".'

    if error:
        lista = conn.execute("SELECT * FROM promociones ORDER BY activo DESC, codigo").fetchall()
        conn.close()
        return render_template("promociones.html", promociones=lista, error=error)

    conn.close()
    return redirect("/promociones")


@app.route("/promociones/toggle/<int:id>")
@login_required
def promociones_toggle(id):
    conn = get_connection()
    conn.execute("UPDATE promociones SET activo = 1 - activo WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    return redirect("/promociones")


@app.route("/promociones/eliminar/<int:id>")
@login_required
def promociones_eliminar(id):
    conn = get_connection()
    conn.execute("DELETE FROM promociones WHERE id = %s", (id,))
    conn.commit()
    conn.close()
    return redirect("/promociones")


# ============================================================
# Clientes — CRUD
# ============================================================

@app.route("/clientes")
@login_required
def clientes():
    busqueda = request.args.get("q", "").strip()

    conn = get_connection()
    if busqueda:
        comodin = f"%{busqueda}%"
        lista = conn.execute("""
            SELECT * FROM clientes
            WHERE nombre ILIKE %s OR telefono ILIKE %s OR email ILIKE %s
            ORDER BY nombre
        """, (comodin, comodin, comodin)).fetchall()
    else:
        lista = conn.execute("SELECT * FROM clientes ORDER BY nombre").fetchall()
    conn.close()

    return render_template("clientes.html", clientes=lista, busqueda=busqueda)


@app.route("/clientes/agregar", methods=["GET", "POST"])
@login_required
def clientes_agregar():
    siguiente = request.args.get("next", "/clientes")

    if request.method == "POST":
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO clientes(nombre, telefono, email, rfc, fecha_registro)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (
            request.form["nombre"].strip(),
            request.form.get("telefono", "").strip(),
            request.form.get("email", "").strip(),
            request.form.get("rfc", "").strip(),
            ahora_local(),
        ))
        nuevo_id = cursor.fetchone()["id"]
        conn.commit()
        conn.close()

        destino = request.form.get("next") or "/clientes"
        separador = "&" if "?" in destino else "?"
        return redirect(f"{destino}{separador}cliente_nuevo={nuevo_id}")

    return render_template("agregar_cliente.html", next=siguiente)


@app.route("/clientes/editar/<int:id>", methods=["GET", "POST"])
@login_required
def clientes_editar(id):
    conn = get_connection()

    if request.method == "POST":
        conn.execute("""
            UPDATE clientes SET nombre = %s, telefono = %s, email = %s, rfc = %s
            WHERE id = %s
        """, (
            request.form["nombre"].strip(),
            request.form.get("telefono", "").strip(),
            request.form.get("email", "").strip(),
            request.form.get("rfc", "").strip(),
            id,
        ))
        conn.commit()
        conn.close()
        return redirect("/clientes")

    cliente = conn.execute("SELECT * FROM clientes WHERE id = %s", (id,)).fetchone()
    conn.close()

    if cliente is None:
        return abort(404)

    return render_template("editar_cliente.html", cliente=cliente)


@app.route("/clientes/eliminar/<int:id>")
@login_required
def clientes_eliminar(id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM clientes WHERE id = %s", (id,))
        conn.commit()
        conn.close()
        return redirect("/clientes")
    except psycopg2.IntegrityError:
        conn.rollback()
        conn.close()
        lista_conn = get_connection()
        lista = lista_conn.execute("SELECT * FROM clientes ORDER BY nombre").fetchall()
        lista_conn.close()
        return render_template(
            "clientes.html", clientes=lista, busqueda="",
            error="No se puede eliminar: este cliente ya tiene ventas registradas. "
                  "Si ya no quieres usarlo, simplemente edítalo o déjalo sin usar."
        )


# ============================================================
# Venta
# ============================================================

@app.route("/venta")
@login_required
def venta():
    filtro_tipo = request.args.get("tipo", "")
    busqueda = request.args.get("q", "").strip()
    cliente_nuevo = request.args.get("cliente_nuevo", "")

    conn = get_connection()
    sql = "SELECT * FROM baterias WHERE stock > 0"
    params = []

    if filtro_tipo:
        sql += " AND tipo_vehiculo = %s"
        params.append(filtro_tipo)

    if busqueda:
        sql += " AND (marca ILIKE %s OR modelo ILIKE %s OR sku ILIKE %s)"
        comodin = f"%{busqueda}%"
        params += [comodin, comodin, comodin]

    sql += " ORDER BY marca, modelo"

    lista = conn.execute(sql, params).fetchall()
    clientes_lista = conn.execute("SELECT * FROM clientes ORDER BY nombre").fetchall()
    conn.close()

    return render_template(
        "venta.html", baterias=lista, tipos=TIPOS_VEHICULO,
        filtro_tipo=filtro_tipo, busqueda=busqueda,
        clientes=clientes_lista, cliente_nuevo=cliente_nuevo
    )


@app.route("/validar_promocion")
@login_required
def validar_promocion():
    codigo = request.args.get("codigo", "").strip().upper()
    conn = get_connection()
    promo = conn.execute(
        "SELECT * FROM promociones WHERE codigo = %s AND activo = 1", (codigo,)
    ).fetchone()
    conn.close()

    if promo is None:
        return {"valido": False}

    return {
        "valido": True,
        "tipo": promo["tipo"],
        "valor": promo["valor"],
        "descripcion": promo["descripcion"],
    }


@app.route("/guardar_venta", methods=["POST"])
@login_required
def guardar_venta():
    detalle = json.loads(request.form["detalle"])
    metodo_pago = request.form["metodo_pago"]
    efectivo = float(request.form.get("efectivo") or 0)
    codigo_promocion = request.form.get("codigo_promocion", "").strip().upper()
    cliente_id = request.form.get("cliente_id") or None
    if cliente_id:
        cliente_id = int(cliente_id)

    conn = get_connection()
    cursor = conn.cursor()

    # 1) Validar stock disponible ANTES de registrar nada
    errores = []
    costos = {}
    for item in detalle:
        fila = cursor.execute(
            "SELECT marca, modelo, stock, precio_proveedor FROM baterias WHERE id = %s", (item["id"],)
        ).fetchone()

        if fila is None:
            errores.append(f'La batería con id {item["id"]} ya no existe.')
        elif fila["stock"] < item["cantidad"]:
            errores.append(
                f'Stock insuficiente de "{fila["marca"]} {fila["modelo"]}": '
                f'quedan {fila["stock"]}, se intentaron vender {item["cantidad"]}.'
            )
        else:
            costos[item["id"]] = fila["precio_proveedor"] or 0

    if errores:
        conn.close()
        return _render_venta_con_error(errores)

    subtotal = sum(item["subtotal"] for item in detalle)

    # 2) Aplicar código de promoción, si viene y es válido
    descuento = 0
    promo = None
    if codigo_promocion:
        promo = cursor.execute(
            "SELECT * FROM promociones WHERE codigo = %s AND activo = 1", (codigo_promocion,)
        ).fetchone()

        if promo is None:
            conn.close()
            return _render_venta_con_error([f'El código "{codigo_promocion}" no es válido o ya no está activo.'])

        if promo["tipo"] == "porcentaje":
            descuento = subtotal * (promo["valor"] / 100)
        else:
            descuento = min(promo["valor"], subtotal)

    total = round(subtotal - descuento, 2)

    if metodo_pago == "Efectivo" and efectivo < total:
        conn.close()
        return _render_venta_con_error([
            f"El efectivo recibido (${efectivo:.2f}) no alcanza para cubrir el total (${total:.2f})."
        ])

    cambio = round(max(0, efectivo - total), 2) if metodo_pago == "Efectivo" else 0

    # Si viene un cliente_id, confirmamos que exista; si no, la venta
    # queda como "Público en general" (cliente_id = NULL) sin tronar.
    if cliente_id:
        existe_cliente = cursor.execute(
            "SELECT 1 FROM clientes WHERE id = %s", (cliente_id,)
        ).fetchone()
        if not existe_cliente:
            cliente_id = None

    # 3) Registrar la venta
    cursor.execute("""
        INSERT INTO ventas(fecha, cliente_id, usuario_id, subtotal, codigo_promocion, descuento, total, metodo_pago, efectivo, cambio)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (ahora_local(), cliente_id, session["usuario_id"], subtotal, codigo_promocion or None, descuento, total, metodo_pago, efectivo, cambio))

    venta_id = cursor.fetchone()["id"]

    for item in detalle:
        costo_unitario = costos.get(item["id"], 0)
        costo_total_linea = costo_unitario * item["cantidad"]

        cursor.execute("""
            INSERT INTO detalle_venta(venta_id, bateria_id, cantidad, precio, costo, subtotal)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (venta_id, item["id"], item["cantidad"], item["precio"], costo_total_linea, item["subtotal"]))

        cursor.execute("""
            UPDATE baterias SET stock = stock - %s
            WHERE id = %s AND stock >= %s
        """, (item["cantidad"], item["id"], item["cantidad"]))

        if cursor.rowcount == 0:
            conn.rollback()
            conn.close()
            return _render_venta_con_error([
                f'Stock insuficiente para el producto id {item["id"]} '
                f'(venta cancelada, alguien más lo vendió justo antes).'
            ])

    conn.commit()
    conn.close()

    return redirect(f"/ticket/{venta_id}")


def _render_venta_con_error(errores):
    conn = get_connection()
    lista = conn.execute("SELECT * FROM baterias WHERE stock > 0 ORDER BY marca, modelo").fetchall()
    clientes_lista = conn.execute("SELECT * FROM clientes ORDER BY nombre").fetchall()
    conn.close()
    return render_template("venta.html", baterias=lista, tipos=TIPOS_VEHICULO,
                            filtro_tipo="", busqueda="", error=errores,
                            clientes=clientes_lista, cliente_nuevo="")


@app.route("/ticket/<int:venta_id>")
@login_required
def ticket(venta_id):
    cliente_nuevo = request.args.get("cliente_nuevo", "")

    conn = get_connection()

    venta = conn.execute("""
        SELECT v.*, c.nombre AS cliente_nombre, c.telefono AS cliente_telefono,
               u.usuario AS vendedor
        FROM ventas v
        LEFT JOIN clientes c ON c.id = v.cliente_id
        LEFT JOIN usuarios u ON u.id = v.usuario_id
        WHERE v.id = %s
    """, (venta_id,)).fetchone()
    if venta is None:
        conn.close()
        return abort(404)

    detalle = conn.execute("""
        SELECT d.*, b.marca, b.modelo, b.sku
        FROM detalle_venta d JOIN baterias b ON b.id = d.bateria_id
        WHERE d.venta_id = %s
    """, (venta_id,)).fetchall()

    clientes_lista = conn.execute("SELECT * FROM clientes ORDER BY nombre").fetchall()

    conn.close()
    return render_template(
        "ticket.html", venta=venta, detalle=detalle,
        clientes=clientes_lista, cliente_nuevo=cliente_nuevo
    )


@app.route("/ticket/<int:venta_id>/folio", methods=["POST"])
@login_required
def ticket_folio(venta_id):
    folio = request.form["folio_factura"].strip()
    conn = get_connection()
    conn.execute("UPDATE ventas SET folio_factura = %s WHERE id = %s", (folio, venta_id))
    conn.commit()
    conn.close()
    return redirect(f"/ticket/{venta_id}")


@app.route("/ticket/<int:venta_id>/cliente", methods=["POST"])
@login_required
def ticket_cliente(venta_id):
    cliente_id = request.form.get("cliente_id") or None
    if cliente_id:
        cliente_id = int(cliente_id)

    conn = get_connection()

    if cliente_id:
        existe = conn.execute("SELECT 1 FROM clientes WHERE id = %s", (cliente_id,)).fetchone()
        if not existe:
            cliente_id = None

    conn.execute("UPDATE ventas SET cliente_id = %s WHERE id = %s", (cliente_id, venta_id))
    conn.commit()
    conn.close()
    return redirect(f"/ticket/{venta_id}")


# ============================================================
# Ventas — historial con filtros por fecha y por modelo
# ============================================================

@app.route("/ventas")
@login_required
def ventas():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    bateria_id = request.args.get("bateria_id", "")
    bateria_id_int = int(bateria_id) if bateria_id else None

    conn = get_connection()

    sql = """
        SELECT DISTINCT v.*, c.nombre AS cliente_nombre, u.usuario AS vendedor,
               COALESCE((SELECT SUM(d2.costo) FROM detalle_venta d2 WHERE d2.venta_id = v.id), 0) AS costo_total
        FROM ventas v
        LEFT JOIN clientes c ON c.id = v.cliente_id
        LEFT JOIN usuarios u ON u.id = v.usuario_id
    """
    condiciones = []
    params = []

    if bateria_id:
        sql += " JOIN detalle_venta d ON d.venta_id = v.id "
        condiciones.append("d.bateria_id = %s")
        params.append(bateria_id_int)

    if desde:
        condiciones.append("DATE(v.fecha) >= DATE(%s)")
        params.append(desde)

    if hasta:
        condiciones.append("DATE(v.fecha) <= DATE(%s)")
        params.append(hasta)

    if condiciones:
        sql += " WHERE " + " AND ".join(condiciones)

    sql += " ORDER BY v.fecha DESC LIMIT 300"

    lista = conn.execute(sql, params).fetchall()

    total_filtrado = sum(v["total"] for v in lista)
    utilidad_filtrada = sum(v["total"] - v["costo_total"] for v in lista)

    baterias_para_filtro = conn.execute("SELECT * FROM baterias ORDER BY marca, modelo").fetchall()

    conn.close()

    return render_template(
        "ventas.html", ventas=lista, total_filtrado=total_filtrado,
        utilidad_filtrada=utilidad_filtrada,
        baterias=baterias_para_filtro, desde=desde, hasta=hasta, bateria_id=bateria_id
    )


# ============================================================
# Reportes
# ============================================================

@app.route("/reportes")
@login_required
def reportes():
    return render_template("reportes.html")


@app.route("/reportes/general")
@login_required
def reporte_general():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")

    conn = get_connection()

    sql = """
        SELECT v.*, c.nombre AS cliente_nombre,
               COALESCE((SELECT SUM(d.costo) FROM detalle_venta d WHERE d.venta_id = v.id), 0) AS costo_total
        FROM ventas v
        LEFT JOIN clientes c ON c.id = v.cliente_id
    """
    condiciones = []
    params = []

    if desde:
        condiciones.append("DATE(v.fecha) >= DATE(%s)")
        params.append(desde)
    if hasta:
        condiciones.append("DATE(v.fecha) <= DATE(%s)")
        params.append(hasta)

    if condiciones:
        sql += " WHERE " + " AND ".join(condiciones)

    sql += " ORDER BY v.fecha DESC LIMIT 500"

    ventas_filtradas = conn.execute(sql, params).fetchall()

    total_vendido = sum(v["total"] for v in ventas_filtradas)
    total_descuentos = sum(v["descuento"] or 0 for v in ventas_filtradas)
    total_costo = sum(v["costo_total"] for v in ventas_filtradas)
    total_utilidad = total_vendido - total_costo
    cantidad = len(ventas_filtradas)
    promedio = (total_vendido / cantidad) if cantidad else 0

    por_metodo = {}
    for v in ventas_filtradas:
        metodo = v["metodo_pago"] or "Sin especificar"
        por_metodo.setdefault(metodo, {"cantidad": 0, "total": 0})
        por_metodo[metodo]["cantidad"] += 1
        por_metodo[metodo]["total"] += v["total"]

    conn.close()

    return render_template(
        "reporte_general.html",
        ventas=ventas_filtradas, total_vendido=total_vendido,
        total_descuentos=total_descuentos, total_utilidad=total_utilidad,
        cantidad=cantidad, promedio=promedio,
        por_metodo=por_metodo, desde=desde, hasta=hasta
    )


@app.route("/reportes/clientes")
@login_required
def reporte_clientes():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")

    conn = get_connection()

    sql = """
        SELECT v.fecha, v.total, v.metodo_pago,
               c.nombre AS cliente_nombre, c.telefono AS cliente_telefono
        FROM ventas v
        JOIN clientes c ON c.id = v.cliente_id
    """
    condiciones = []
    params = []

    if desde:
        condiciones.append("DATE(v.fecha) >= DATE(%s)")
        params.append(desde)
    if hasta:
        condiciones.append("DATE(v.fecha) <= DATE(%s)")
        params.append(hasta)

    if condiciones:
        sql += " WHERE " + " AND ".join(condiciones)

    sql += " ORDER BY v.fecha DESC LIMIT 500"

    filas = conn.execute(sql, params).fetchall()
    total = sum(f["total"] for f in filas)
    conn.close()

    return render_template(
        "reporte_clientes.html", filas=filas, total=total, desde=desde, hasta=hasta
    )


@app.route("/reportes/baterias")
@login_required
def reporte_baterias():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")

    conn = get_connection()

    sql = """
        SELECT b.marca, b.modelo, b.tipo_vehiculo, b.sku,
               SUM(d.cantidad) AS unidades,
               SUM(d.subtotal) AS ingresos_lista,
               SUM(d.costo) AS costo_total,
               SUM(
                   CASE WHEN v.subtotal > 0
                        THEN d.subtotal * (v.total * 1.0 / v.subtotal)
                        ELSE d.subtotal
                   END
               ) AS ingresos
        FROM detalle_venta d
        JOIN baterias b ON b.id = d.bateria_id
        JOIN ventas v ON v.id = d.venta_id
    """
    condiciones = []
    params = []

    if desde:
        condiciones.append("DATE(v.fecha) >= DATE(%s)")
        params.append(desde)
    if hasta:
        condiciones.append("DATE(v.fecha) <= DATE(%s)")
        params.append(hasta)

    if condiciones:
        sql += " WHERE " + " AND ".join(condiciones)

    sql += " GROUP BY b.id ORDER BY unidades DESC"

    filas = conn.execute(sql, params).fetchall()
    total_unidades = sum(f["unidades"] for f in filas)
    total_ingresos_lista = sum(f["ingresos_lista"] for f in filas)
    total_ingresos = sum(f["ingresos"] for f in filas)
    total_costo = sum(f["costo_total"] for f in filas)
    total_utilidad = total_ingresos - total_costo
    conn.close()

    return render_template(
        "reporte_baterias.html", filas=filas, total_unidades=total_unidades,
        total_ingresos=total_ingresos, total_ingresos_lista=total_ingresos_lista,
        total_costo=total_costo, total_utilidad=total_utilidad,
        desde=desde, hasta=hasta
    )


if __name__ == "__main__":
    # host="0.0.0.0" para aceptar conexiones desde otros dispositivos,
    # no solo de esta misma máquina. El puerto lo asigna la plataforma
    # de despliegue (Railway, etc.) mediante la variable PORT; 5000 es
    # solo el valor de respaldo para pruebas locales.
    puerto = int(os.environ.get("PORT", 5000))
    modo_debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=puerto, debug=modo_debug, threaded=True)
