# BateriasPro 2.0 — versión en la nube (multi-dispositivo)

Esta es la versión de BateriasPro pensada para vivir en internet y que
cualquier dispositivo (celular, tablet, otra computadora) pueda
conectarse desde cualquier lugar — no solo desde la computadora del
negocio.

## Qué cambió respecto a la versión 1 (de escritorio)

- **Base de datos:** de SQLite (un archivo local) a **PostgreSQL** (un
  servidor de base de datos real, apto para que varios dispositivos
  escriban al mismo tiempo desde internet sin perder información).
- **Login obligatorio:** ya no se entra directo. Es la misma lógica de
  usuario/contraseña que ya conoces de PVtienda, pero aquí **todos los
  usuarios tienen el mismo nivel de acceso** (no hay roles admin/vendedor).
  Se agregó porque, al tener una dirección pública en internet, cualquiera
  podría entrar sin esto.
- **Cada venta queda registrada a nombre de quien la hizo** (columna
  "Vendedor" en el historial y en el ticket).
- **El servidor ya no es el de desarrollo de Flask** — corre con
  **Gunicorn**, que sí está pensado para producción.

## Antes de desplegar: prueba local (opcional pero recomendado)

1. Instala las dependencias:
   ```
   pip install -r requirements.txt
   ```
2. Copia `.env.example` a `.env` y llena `DATABASE_URL` con una base de
   datos PostgreSQL (puede ser una que ya tengas en Railway, o una local
   si tienes PostgreSQL instalado).
3. Corre:
   ```
   python app.py
   ```
4. Entra a `http://127.0.0.1:5000/login` — usuario `admin`, contraseña
   `admin123`.

## Desplegar en Railway (recomendado para empezar)

1. **Crea una cuenta** en [railway.app](https://railway.app) (tiene un
   plan de prueba gratis).
2. **Nuevo proyecto → Deploy from GitHub repo.** Necesitas subir esta
   carpeta a un repositorio de GitHub primero (puede ser privado). Si
   nunca has usado GitHub, dime y te explico ese paso también.
3. Dentro del proyecto en Railway, da clic en **"+ New" → Database →
   Add PostgreSQL.** Railway crea la base de datos y genera
   automáticamente la variable `DATABASE_URL` — **no la escribas tú a
   mano**, Railway la conecta sola a tu servicio web.
4. En la pestaña **Variables** de tu servicio web (no de la base de
   datos), agrega:
   - `SECRET_KEY` — genera una con
     `python -c "import secrets; print(secrets.token_hex(32))"`
     y pégala aquí.
5. Railway detecta el `Procfile` y el `requirements.txt` solo, y
   despliega automáticamente. Cuando termine, te da una URL pública
   como `https://tu-proyecto.up.railway.app`.
6. Entra a esa URL, inicia sesión con `admin` / `admin123`, y **cámbiala
   de inmediato** desde "Usuarios".

## Después de desplegar

- **Respaldos:** en el plan pagado de Railway puedes activar respaldos
  automáticos de la base de datos desde su panel. Revísalo — con un
  negocio real dependiendo de esto, no querrás depender solo de la
  suerte.
- **Dominio propio:** Railway te deja conectar tu propio dominio
  (por ejemplo `sistema.tunegocio.com`) desde Settings → Domains, si
  más adelante quieres una dirección con tu marca en vez de la de
  Railway.
- **Actualizar el código:** cada vez que quieras subir cambios, los
  subes a tu repositorio de GitHub y Railway vuelve a desplegar solo.

## Diferencias a tener en cuenta

- La hora de las ventas se calcula explícitamente en la zona horaria
  de Ciudad de México (`America/Mexico_City`), sin importar en qué
  región del mundo esté el servidor de Railway — no deberías ver
  horas raras aunque el servidor físico esté en otro país.
- El buscador de clientes y de baterías ahora no distingue mayúsculas
  ni minúsculas (igual que antes), pero técnicamente funciona distinto
  por debajo (`ILIKE` en vez de `LIKE`) — es un detalle de PostgreSQL,
  no debería notarse en el uso diario.
