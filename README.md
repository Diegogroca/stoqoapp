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
| Linter | ruff | Detecta imports sin usar y errores estaticos antes de ejecutar |
| CI | GitHub Actions | Linter, compilacion de plantillas y pruebas en cada push |
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
servicios/           Logica de negocio: cuentas, productos, movimientos,
                     indicadores, reportes y exportaciones.
rutas/               Pantallas y formularios (routers de FastAPI).
migrations/          Esquema SQL que se ejecuta en Supabase.
templates/           Plantillas Jinja2 (base.html define los tokens de diseño).
tests/               Un archivo de pruebas por etapa.
docs/bitacora-ia.md  Prompts, resultados y correcciones del trabajo con IA.
docs/complejidad.md  Complejidad planeada contra implementada, con mediciones.
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
| 4 | Motor de movimientos | Listo |
| 5 | Dashboard y alertas | Listo |
| 6 | Reportes, filtros y exportaciones | Listo |
| 7 | Calidad de interfaz | Listo |
| 8 | Pruebas y demostracion con KOVA | Listo |

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
- **Prepared statements desactivados (`prepare_threshold=None`).** psycopg3 crea
  prepared statements del lado del servidor con nombres correlativos, y el pooler
  de Supabase reutiliza la misma conexion de servidor entre peticiones distintas:
  la segunda intenta declarar un nombre que ya existe y Postgres devuelve
  `DuplicatePreparedStatement`. Se pierde una optimizacion menor y a cambio la
  aplicacion funciona detras del pooler, que es obligatorio en serverless.
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
- **Se corrigio un problema N+1 medido, no supuesto.** El catalogo resolvia la
  descripcion de cada variante con una consulta por variante: 50 consultas para
  pintar el caso KOVA de 49 variantes. Ahora son 4, y el numero no crece con el
  inventario. Las mediciones y el analisis estan en
  [`docs/complejidad.md`](docs/complejidad.md); `tests/test_eficiencia_consultas.py`
  cuenta las consultas reales y falla si alguien reintroduce el patron.
- **El historial pagina y dice cuantos hay.** Antes cortaba en 300 filas en
  silencio: un usuario con 400 movimientos creia ver todo su historial y no era
  cierto. Un limite invisible es peor que una pantalla que informa el total,
  porque se toman decisiones sobre datos incompletos sin saberlo.
- **Una sola consulta para pantalla, Excel y PDF.** Cada reporte es una funcion
  que devuelve un objeto con titulo, columnas y filas; las tres salidas consumen
  ese mismo objeto con los mismos filtros. No existen dos consultas que puedan
  divergir, asi que el archivo descargado no puede mostrar un subconjunto distinto
  del que se ve en pantalla. Es la mitigacion del riesgo "exportaciones distintas
  a los filtros" resuelta por construccion y no por disciplina.
- **En Excel los numeros son numeros.** El formato de moneda se aplica al estilo
  de la celda, no al valor: una columna con el texto "$1,250.00" es inservible
  para sumar o graficar.
- **En movil las tablas se desplazan, no se recortan.** Esconder columnas en
  pantallas chicas ocultaria datos del inventario; el contenedor permite
  desplazamiento horizontal para que se vea todo.
- **Cancelar no borra: compensa.** El movimiento original se marca como cancelado
  y se crea uno nuevo con el tipo opuesto, enlazado al primero. El historial
  conserva el error, la correccion y el stock final. Un original admite una sola
  compensacion (unique en la base) y una compensacion no se puede cancelar.
- **Los rankings excluyen cancelaciones.** Una correccion contable no es mercancia
  que se movio, asi que los indicadores de actividad filtran cancelados y
  compensaciones. Siguen visibles en el historial de auditoria.
- **El volumen usa valores absolutos.** Un producto que entro 100 y salio 100 tuvo
  mucha actividad aunque su neto sea cero.
- **El minimo pertenece al producto, no a la variante.** Se compara contra la suma
  de las variantes activas: 6 en talla M mas 6 en L son 12 unidades y no estan
  bajo un minimo de 10, aunque cada variante suelta parezca escasa.
- **Los indicadores se calculan en SQL.** Agregaciones con GROUP BY y sumas
  condicionales en lugar de traer todas las filas a memoria: en una funcion
  serverless la memoria es limitada y Postgres suma con indice.
- **Eliminar es retirar, no borrar (CE-18).** Un producto retirado queda inactivo
  pero sus filas siguen existiendo, asi que los movimientos pasados conservan su
  SKU, su nombre y sus cifras y un reporte antiguo sigue siendo legible. Un DELETE
  real seria rechazado por las llaves foraneas de los movimientos.
- **Los atributos no se editan.** Renombrar "Talla" o quitar un color obligaria a
  reescribir el historial o a dejar movimientos huerfanos. Para cambiar la
  estructura se crea un producto nuevo y se retira el anterior.
- **Los errores se muestran en pantalla.** Un fallo en produccion devolvia un
  "Internal Server Error" vacio y habia que ir a los logs de la plataforma. Ahora
  la aplicacion muestra el tipo y el mensaje del error (nunca el traceback ni las
  variables de entorno), de modo que se puede diagnosticar desde el navegador.
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

## Criterios de exito

Los veinte criterios de la seccion 5 de la planeacion estan implementados como
pruebas automatizadas en `tests/test_criterios_exito.py`, en el mismo orden y con
la misma numeracion. La salida de `pytest -v` funciona como evidencia directa:

```bash
pytest tests/test_criterios_exito.py -v
```

El CI de GitHub Actions ejecuta cuatro controles en cada push: linter (`ruff`),
compilacion de las plantillas Jinja2, la suite completa y los criterios de exito
por separado para que su resultado quede legible en el registro del workflow.

| Criterio | Que verifica |
|---|---|
| CE-01 | Una empresa nueva no puede consultar datos de otra |
| CE-02 | Onboarding: de cuenta nueva a inventario inicial coherente |
| CE-03 | Producto simple con SKU unico y una variante base |
| CE-04 | Dos atributos de 7 valores generan 49 combinaciones unicas |
| CE-05 | Variantes manuales sin generar todas las posibles |
| CE-06 | Una entrada de 10 aumenta el stock exactamente en 10 |
| CE-07 | Una salida de 2 disminuye el stock exactamente en 2 |
| CE-08 | Ajustes positivos y negativos aplican el delta correcto |
| CE-09 | Stock negativo: sin confirmar no cambia; confirmado exige motivo |
| CE-10 | Cancelar compensa, restaura el stock y no se puede repetir |
| CE-11 | Los datos persisten tras cerrar y reabrir sesion |
| CE-12 | Las metricas coinciden con el calculo manual |
| CE-13 | Solo los productos bajo su minimo aparecen en alertas |
| CE-14 | Los filtros cambian el subconjunto mostrado |
| CE-15 | Los seis reportes abren y manejan periodos vacios |
| CE-16 | El Excel abre, esta organizado y refleja el filtro |
| CE-17 | El PDF se descarga legible con el mismo filtro |
| CE-18 | Retirar conserva la identidad del producto en el historial |
| CE-19 | Las rutas principales responden en computadora y celular |
| CE-20 | Ninguna ruta rota ni operaciones parciales |

## Autoevaluacion

**Lo que quedo solido.** El nucleo de trazabilidad. Ningun modulo salvo
`servicios/movimientos.py` modifica el stock de una variante, y ese modulo lee con
bloqueo de fila, deriva el signo del tipo de movimiento y confirma movimiento y
stock en la misma transaccion. Eso hace que cada cifra del dashboard sea
reconstruible desde su historial, que era la promesa central del proyecto. El
aislamiento multiempresa tambien quedo firme porque no depende de que yo me
acuerde de filtrar: las llaves foraneas compuestas lo impiden en la base de datos.

**Lo que quedo debil.** El costo unitario es un solo campo por producto, y eso es
un error contable de fondo que reconozco: si compro 100 polos a $200 y despues 100
a $260, el valor a costo que muestra el sistema deja de ser correcto desde ese
momento. Un sistema real usa costo promedio ponderado o PEPS y lo recalcula en cada
entrada. No es cosmetico, porque la cifra sobre la que el dueño toma decisiones
queda desviada.

Las pruebas corren sobre SQLite y la aplicacion en
produccion sobre Postgres detras de un pooler. Esa diferencia me costo un fallo
real (`DuplicatePreparedStatement`) que ninguna prueba podia detectar. La decision
sigue siendo defendible —el CI no debe tener credenciales de produccion y una
prueba con red no es repetible— pero el limite es real y lo tengo documentado. El
otro punto debil es que los atributos de un producto no se pueden editar: es una
decision consciente para no romper la trazabilidad, pero en un producto comercial
haria falta una migracion de variantes.

**Trabajo futuro, en orden de prioridad.**

1. **Costo promedio ponderado.** Es la correccion mas importante porque afecta la
   exactitud de una cifra de negocio, no solo la comodidad.
2. **Usuarios con roles** (dueño, almacenista, vendedor). Sin ellos la
   trazabilidad dice que paso pero no QUIEN lo hizo, que es justo lo que se
   pregunta cuando falta mercancia.
3. **Captura por WhatsApp.** El obstaculo real de cualquier inventario manual es
   el abandono: si el registro no ocurre, las cifras dejan de cuadrar y se vuelve a
   Excel. Nadie abre una app web en medio de una venta, pero WhatsApp ya esta
   abierto.
4. **Codigo de barras con la camara del celular** e importacion masiva desde
   Excel: nadie captura 500 SKUs a mano.
5. **Reporte de fuga.** Stoqo ya guarda incidencias y ajustes negativos con
   motivo, algo que las herramientas de este segmento no hacen con esta
   disciplina. Con esos datos se puede responder cuanta mercancia desaparecio en
   un periodo y donde se concentra, que es informacion por la que un dueño paga.
6. **Prediccion de agotado** en lugar de minimos fijos: el historial permite
   calcular velocidad de venta por variante y estimar en cuantos dias se agota.
7. Proveedores y ordenes de compra, multiples almacenes, conteo ciclico.

**Sobre el alcance elegido.** El MVP se planteo para "cualquier negocio con
productos fisicos", y esa amplitud es su punto mas debil como producto: una
herramienta generica rara vez es mejor que Excel para alguien en particular. La
parte mas fuerte del sistema es el manejo de atributos y variantes, que es
precisamente donde las herramientas genericas fallan. Un producto comercial deberia
enfocarse en marcas de ropa y calzado con 50 a 500 SKUs, que es el caso KOVA.

**Que haria distinto.** Probaria la aplicacion desplegada despues de cada etapa y
no solo al final del bloque. Los dos errores mas caros del proyecto —la cantidad
unica para 49 variantes y el `{% elif %}` invalido— aparecieron al usar la app, no
al leer el codigo. Tambien habria agregado la pantalla de error y la prueba de
compilacion de plantillas desde la Etapa 0: las dos surgieron como reaccion a un
fallo, cuando debieron ser preventivas.

**Donde la IA ayudo y donde estorbo.** Ayudo a traducir el pseudocodigo de mi
planeacion a implementaciones con transacciones y restricciones correctas mucho mas
rapido de lo que yo lo habria hecho, y detecto una contradiccion en mi propio
documento (Streamlit no despliega en Vercel) antes de que me costara tiempo.
Estorbo cuando implemento literalmente lo que decia mi planeacion sin notar que era
incompatible con el resto de mi modelo: "existencia inicial" como un solo campo es
coherente con la frase de mi documento y absurdo con 49 variantes. La correccion
salio de probar la app, no de revisar el codigo. El registro completo esta en
[`docs/bitacora-ia.md`](docs/bitacora-ia.md).
