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
| 0 | Esqueleto y despliegue en Vercel | En curso |
| 1 | Modelo de datos y aislamiento por empresa | Pendiente |
| 2 | Registro de cuenta y onboarding | Pendiente |
| 3 | Productos, atributos y variantes | Pendiente |
| 4 | Motor de movimientos | Pendiente |
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
  abren y cierran en cada invocacion; la conexion directa agota el limite.

---

## Prompts principales utilizados con IA

Registro completo en [`docs/bitacora-ia.md`](docs/bitacora-ia.md), incluyendo
las limitaciones y errores detectados en las respuestas de la IA y como se
corrigieron.

---

## Autoevaluacion

_(Se escribe al cerrar el proyecto: que quedo solido, que quedo debil, que
haria distinto y donde la IA ayudo o estorbo.)_
