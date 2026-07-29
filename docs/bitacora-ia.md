# Bitacora de uso de IA

Registro del trabajo con la IA como copiloto: que le pedi, que devolvio, que
tuve que corregir y por que. Se llena **durante** el desarrollo, no al final.

Formato de cada entrada:

- **Etapa:** en que parte del proyecto estaba.
- **Prompt:** lo que pedi, resumido pero fiel.
- **Resultado:** que devolvio.
- **Correccion o limitacion:** que estaba mal, incompleto o alucinado, y como lo resolvi.

---

## Entrada 1 - Seleccion del stack

- **Etapa:** 0. Cierre del checkpoint tecnologico de la Fase 1.
- **Prompt:** Pedi ayuda para llevar mi documento de planeacion a una app web
  desplegada en Vercel, con Python como lenguaje central.
- **Resultado:** Se detecto un conflicto en mi propia planeacion: la
  recomendacion provisional (Streamlit) no puede desplegarse en Vercel, porque
  Streamlit necesita un servidor con proceso persistente y Vercel ejecuta
  funciones serverless. Se propuso FastAPI + Jinja2 + Supabase.
- **Correccion o limitacion:** No fue una alucinacion de la IA sino un error de
  mi documento. Verifique la afirmacion en la documentacion de Vercel, que
  confirma soporte nativo para FastAPI buscando una instancia `app` en
  `main.py`. Decision registrada como cierre de la seccion 4.4 de la Fase 1.

## Entrada 2 - Esqueleto de la aplicacion

- **Etapa:** 0. Esqueleto y despliegue.
- **Prompt:** Pedi un esqueleto minimo que despliegue en Vercel antes de escribir
  logica de negocio.
- **Resultado:** `main.py` con dos rutas (`/` y `/health`), plantillas Jinja2 y
  cuatro pruebas de humo.
- **Correccion o limitacion:** Punto a vigilar: en Vercel el directorio de
  trabajo no siempre es la raiz del proyecto, asi que las plantillas se cargan
  con una ruta absoluta derivada de `__file__` en lugar de una ruta relativa.
  Con `Jinja2Templates(directory="templates")` el despliegue puede funcionar en
  local y fallar publicado.

---

## Limitaciones observadas de la IA en este proyecto

<!-- Se llena conforme aparezcan. Ejemplos de que anotar:
     - versiones de librerias que no existen
     - APIs de Supabase o FastAPI que cambiaron
     - codigo que ignora una regla de negocio de mi planeacion
     - soluciones que funcionan en local pero no en serverless -->
