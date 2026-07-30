# Complejidad: lo planeado contra lo implementado

La Fase 1 afirmó complejidades para los algoritmos centrales antes de escribir
código. Este documento comprueba si la implementación las cumple, y donde no lo
hizo, qué se corrigió. Las mediciones son reproducibles con
`pytest tests/test_eficiencia_consultas.py -v`.

Nota metodológica: en una aplicación que habla con una base de datos por red, la
métrica que importa no es el número de operaciones en Python sino el **número de
consultas**. Cada consulta desde una función serverless de Vercel hacia el pooler
de Supabase es un viaje de ida y vuelta, y la latencia de ese viaje domina sobre
cualquier cálculo local. Cincuenta consultas no son cincuenta veces más lentas
que una: son peor, porque se pagan cincuenta latencias en serie.

También es una métrica **determinista**: cronometrar depende de la máquina y de la
red, contar consultas da el mismo resultado siempre. Por eso las pruebas cuentan
en lugar de medir tiempo.

---

## 1. Creación de un producto con variantes

**Planeado (sección 3.2):** O(v × a), donde v es el número de variantes y a el de
atributos por combinación. La generación automática produce
v = n₁ × n₂ × ... × nₖ combinaciones.

**Implementado:** se cumple. `servicios/productos.py` recorre el producto
cartesiano una vez y crea una variante por combinación, más un enlace por valor de
atributo. El caso KOVA con 7 × 7 genera 49 variantes y 98 enlaces.

**Riesgo confirmado en la práctica.** El crecimiento es multiplicativo, no lineal:
7 × 7 = 49, pero 30 × 30 = 900. La mitigación de la planeación se implementó en
dos capas: `contar_combinaciones()` calcula el total **sin tocar la base de datos**
(aritmética pura) y el servicio rechaza cualquier configuración por encima de
`MAXIMO_VARIANTES = 300` antes de escribir una sola fila. La pantalla muestra el
total mientras el usuario escribe.

---

## 2. Registro de un movimiento

**Planeado (sección 3.3):** O(1) respecto al número de productos, con índices por
empresa y SKU.

**Implementado:** se cumple. `registrar_movimiento()` ejecuta un `SELECT` de una
fila con bloqueo (`FOR UPDATE`), un `INSERT` y un `UPDATE`. Ninguna de las tres
depende del tamaño del inventario, porque la variante se localiza por clave
primaria.

**Precisión sobre el bloqueo.** La planeación pedía transacciones atómicas para
"impedir que dos operaciones simultáneas calculen el mismo stock anterior". Eso no
lo garantiza la transacción por sí sola: una transacción aislada puede leer un
valor obsoleto. Lo que lo garantiza es el `SELECT ... FOR UPDATE`, que bloquea la
fila hasta que la transacción termina. La segunda operación espera y lee el stock
ya actualizado.

---

## 3. Alertas y estados de reposición

**Planeado (sección 3.5):** O(v + p), donde v son las variantes y p los productos:
primero se agregan existencias por producto y luego se compara cada total contra su
mínimo.

**Implementado:** mejor que lo planeado en número de consultas, igual en trabajo
total. `estado_por_producto()` no recorre las variantes en Python: usa un
`GROUP BY` con `SUM` que Postgres resuelve con el índice
`idx_variantes_empresa`. La agregación sigue siendo O(v + p) en trabajo, pero
ocurre dentro de la base de datos y viaja en **una sola** consulta en lugar de p+1.

---

## 4. El problema que la planeación no previó: N+1

Aquí está la discrepancia real entre lo planeado y lo implementado, y la razón
principal de este documento.

**Qué pasó.** La primera implementación del catálogo resolvía la descripción de
cada variante ("M / negro") con una función que consultaba **una variante a la
vez**. Se llamaba dentro de un bucle. El resultado con el caso KOVA:

| Escenario | Consultas para pintar el catálogo |
|---|---|
| Patrón original (una por variante) | **50** |
| Después de la corrección | **4** |

Se llama N+1: una consulta para traer la lista, más una por cada elemento de esa
lista. Es uno de los antipatrones más comunes con un ORM, porque el código se lee
perfectamente natural —`for v in variantes: descripcion(v)`— y no hay nada
sintácticamente sospechoso en él. El coste solo aparece al medir.

**Por qué la planeación no lo detectó.** La sección 3 analizó la complejidad de los
algoritmos de negocio (crear, mover, alertar) pero no la de **renderizar una
pantalla**, que resultó ser la operación más frecuente de toda la aplicación. Un
usuario crea un producto una vez y abre el catálogo cincuenta veces al día.

**Cómo se corrigió.** `descripciones_de(sesion, ids)` trae todas las parejas
(variante, valor) en una consulta con `JOIN` y las agrupa en memoria. El mismo
patrón se aplicó al catálogo (todas las variantes de todos los productos en una
consulta), al historial y al panel.

Detalle de portabilidad: se agrupa en Python en lugar de usar `string_agg`
(Postgres) o `group_concat` (SQLite) porque esas funciones se escriben distinto en
cada motor, y las pruebas corren sobre SQLite mientras producción corre sobre
Postgres.

**Estado actual medido:**

| Pantalla | Consultas | Depende del tamaño del inventario |
|---|---|---|
| Catálogo (49 variantes) | 4 | No |
| Panel (6 indicadores) | 11 | No |
| Historial (50 filas) | 7 | No |

El panel usa más porque cada indicador es una agregación distinta por naturaleza;
lo relevante es que ninguna de las tres crece con el inventario.

**Prueba de regresión.** `tests/test_eficiencia_consultas.py` cuenta las consultas
reales enchufándose al evento `before_cursor_execute` de SQLAlchemy y falla si el
catálogo supera 15. Reintroducir el patrón anterior daría más de 50 y la prueba se
pondría roja en CI. Otra prueba compara el catálogo con 1 producto contra 4 y exige
que el número de consultas casi no cambie: es la diferencia entre coste constante y
coste lineal.

---

## 5. Índices y su justificación

Los índices de `migrations/001_esquema_inicial.sql` no son decorativos; cada uno
responde a un patrón de consulta concreto:

| Índice | Consulta que acelera |
|---|---|
| `idx_movimientos_empresa_fecha` | Historial y actividad reciente: filtran por empresa y ordenan por fecha descendente |
| `idx_movimientos_variante` | Reconstruir el historial de una variante |
| `idx_variantes_empresa` | Suma de stock por empresa (unidades disponibles, valor) |
| `idx_productos_empresa` | Catálogo y reportes filtrados por empresa |

El patrón común es que **todos empiezan por `empresa_id`**. No es casualidad: en
una aplicación multiempresa ninguna consulta legítima omite ese filtro, así que
ponerlo como primera columna del índice hace que sirva para prácticamente
cualquier consulta del sistema.

---

## 6. Lo que sigue siendo O(n) y por qué se aceptó

- **Historial paginado.** El `COUNT` para saber el total recorre las filas que
  pasan el filtro. Con decenas de miles de movimientos convendría una estimación
  en lugar de un conteo exacto, pero para el volumen de una PyME el conteo exacto
  es preferible: decirle al usuario "página 2 de 9" vale más que ahorrar
  milisegundos.
- **Generación de Excel y PDF.** Materializan todas las filas del reporte en
  memoria. Es lineal en el número de registros y está acotado por el tamaño del
  inventario de una empresa. Para catálogos muy grandes haría falta escritura por
  lotes o generación en segundo plano; queda como trabajo futuro documentado.

---

## Conclusión

De las tres complejidades afirmadas en la planeación, las tres se cumplen. Lo que
la planeación no anticipó fue el coste de las pantallas, que resultó ser la
operación más frecuente y contenía un problema N+1 que multiplicaba por doce el
número de viajes a la base de datos.

La lección metodológica: analizar la complejidad de los algoritmos no equivale a
analizar el rendimiento del sistema. Un ORM esconde las consultas detrás de código
que se lee natural, y la única forma de saber cuántas se lanzan es medirlas.
