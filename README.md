# Stoqo

Plataforma multiempresa de control de inventario para negocios que venden
productos fisicos. Proyecto final de **Python para negocios**.

**Estudiante:** Diego Gomez
**Caso demostrativo:** KOVA, marca de polos premium
**App desplegada:** https://stoqoapp.vercel.app

---

## Que resuelve

Un negocio pequeño registra existencias en libretas, mensajes y hojas de
calculo separadas. Cuando la informacion se actualiza tarde, la persona
propietaria no puede responder cuanto inventario tiene, que se esta moviendo ni
que debe reponer.

Stoqo mantiene **una sola version del inventario** y, sobre todo, explica por
que cambio: cada entrada, salida y ajuste guarda el stock anterior, el delta
firmado y el stock posterior. Ninguna cifra del dashboard aparece sin poder
reconstruirse desde su historial.

Fuera del alcance de este MVP: punto de venta, clientes y proveedores, multiples
usuarios o sucursales, facturacion y notificaciones.

---

## Stack tecnologico

| Capa | Herramienta | Por que esta aqui |
|---|---|---|
| Lenguaje | Python 3.12 | Toda la logica de negocio, validaciones y calculos viven en Python |
| Servidor web | FastAPI | ASGI, validacion por tipos y despliegue sin configuracion en Vercel |
| Interfaz | Jinja2 + CSS propio | HTML renderizado desde Python; sin proceso de build de JavaScript |
| Base de datos | Supabase (PostgreSQL) | Persistencia, indices, transacciones reales y aislamiento por empresa |
| Validacion | Pydantic | Centraliza reglas: cantidades enteras, costo no negativo, campos obligatorios |
| Excel | openpyxl | Exportaciones sin dependencias de sistema |
| PDF | ReportLab | Reportes descargables con formato controlado |
| Pruebas | pytest | Cada criterio de exito (CE-01 a CE-20) se vuelve una prueba |
| CI | GitHub Actions | Ejecuta las pruebas en cada push antes de que Vercel publique |
| Despliegue | Vercel | Funcion serverless de Python, despliegue automatico desde `main` |

### Por que no Streamlit

La planeacion (Fase 1) proponia Streamlit como candidato principal. Se descarto
al cerrar el checkpoint tecnologico: **Streamlit necesita un servidor con proceso
persistente y Vercel ejecuta funciones serverless**, por lo que era incompatible
con el entregable obligatorio de despliegue en Vercel. FastAPI conserva el mismo
objetivo academico (Python visible y explicable) y si es soportado de forma
nativa por la plataforma.

---

## Estructura del proyecto

```
main.py              Entrypoint. Vercel busca la instancia `app` aqui.
modelos.py           Modelos SQLAlchemy: el esquema declarado en Python.
db.py                Conexion a Postgres, preparada para serverless.
alcance.py           Capa que acota toda lectura a la empresa autenticada.
seguridad.py         Hash de contraseñas y firma de cookies de sesion.
dependencias.py      Sesion de base de datos y empresa autenticada por peticion.
vistas.py            Configuracion unica de las plantillas Jinja2.
servicios/           Logica de negocio: cuentas, productos y movimientos.
rutas/               Pantallas y formularios (routers de FastAPI).
migrations/          Esquema SQL que se ejecuta en Supabase.
templates/           Plantillas Jinja2 (base.html define los tokens de diseño).
tests/               Un archivo de pruebas por etapa.
docs/bitacora-ia.md  Prompts, resultados y correcciones del trabajo con IA.
.github/workflows/   Pruebas automaticas en cada push.
requirements.txt     Dependencias que instala Vercel.
requirements-dev.txt Dependencias de desarrollo y CI.
.env.example         Variables de entorno necesarias (sin valores reales).
```

---

## Correr en local

```bash
pip install -r requirements-dev.txt
cp .env.example .env          # y completar los valores
uvicorn main:app --reload
```

Abrir http://127.0.0.1:8000

Pruebas:

```bash
pytest -v
```

---

## Etapas y estado

| # | Etapa | Estado |
|---|---|---|
| 0 | Esqueleto y despliegue en Vercel | Listo |
| 1 | Modelo de datos y aislamiento por empresa | Listo |
| 2 | Registro de cuenta y onboarding | Listo |
| 3 | Productos, atributos y variantes | Listo |
| 4 | Motor de movimientos | En curso (nucleo listo, faltan pantallas) |
| 5 | Dashboard y alertas | Pendiente |
| 6 | Reportes, filtros y exportaciones | Pendiente |
| 7 | Calidad de interfaz | Pendiente |
| 8 | Pruebas y demostracion con KOVA | Pendiente |

El orden responde a dependencias, no a preferencia: los reportes no se
construyen antes de estabilizar el motor de movimientos, porque una cifra
correcta necesita un dato correcto.

---

## Decisiones tecnicas relevantes

- **Rutas absolutas para plantillas.** En Vercel el directorio de trabajo no
  siempre es la raiz del proyecto, asi que `templates/` se resuelve desde
  `Path(__file__).resolve().parent`.
- **Sin secretos en el repositorio.** Las llaves viven en las variables de
  entorno de Vercel. La pantalla de estado solo indica si existen.
- **Pooler en modo transaccion para Supabase.** En serverless las conexiones se
  abren y cierran en cada invocacion; la conexion directa agota el limite. Por
  eso el motor usa `NullPool`: el pool real vive en Supabase, no en la funcion.
- **Aislamiento en dos capas.** Las llaves foraneas compuestas
  `(empresa_id, producto_id)` y `(empresa_id, variante_id)` impiden que la base
  de datos mezcle marcas incluso si el codigo falla. Encima, `AlcanceEmpresa`
  concentra el filtro de lectura en un solo lugar auditable en lugar de
  repetirlo en cada consulta.
- **Reglas de negocio como restricciones, no como comentarios.** SKU unico por
  empresa, un propietario por empresa, costo no negativo, cantidad positiva,
  `stock_posterior = stock_anterior + delta` y una sola compensacion por
  movimiento viven en el esquema. La base rechaza un historial que se
  contradice.
- **Contraseñas y sesiones con la biblioteca estandar.** pbkdf2_hmac con 200.000
  iteraciones y sal por cuenta para las contraseñas; cookie firmada con HMAC para
  las sesiones. Sin dependencias extra que engorden el bundle serverless.
- **La cookie es `secure` segun el esquema de la peticion.** Con `secure=True`
  fijo, la sesion funciona en Vercel pero es imposible probar la app en local o
  desde pytest, porque una cookie secure no viaja por http.
- **Las existencias se capturan variante por variante.** Aplicar una sola
  cantidad a las 49 variantes no describe ningun inventario real: 5 medianas
  azules y 4 grandes azules son cifras distintas. El alta define la estructura y
  una segunda pantalla captura las cantidades, cada una con su propio movimiento.
- **Un solo lugar modifica el stock.** `servicios/movimientos.py` es el unico
  modulo que escribe `variante.stock`. Lee el stock anterior con
  `SELECT ... FOR UPDATE`, deriva el signo del tipo de movimiento (nunca del
  formulario) y confirma movimiento y stock en la misma transaccion.
- **El total de variantes se calcula antes de crearlas.** El producto cartesiano
  crece multiplicativamente (7 x 7 = 49, pero 30 x 30 = 900), asi que se cuenta y
  se valida contra un tope antes de escribir en la base.
- **Las pruebas no tocan Supabase.** Corren sobre SQLite en memoria con el mismo
  modelo declarado. El CI no necesita credenciales de produccion y las pruebas
  son repetibles sin red.

---

## Prompts principales utilizados con IA

Registro completo en [`docs/bitacora-ia.md`](docs/bitacora-ia.md), incluyendo
las limitaciones y errores detectados en las respuestas de la IA y como se
corrigieron.

---

## Autoevaluacion

_(Se escribe al cerrar el proyecto: que quedo solido, que quedo debil, que
haria distinto y donde la IA ayudo o estorbo.)_
