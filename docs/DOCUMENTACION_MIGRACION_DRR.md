# Documentación: Migración e integración DRR APIs con Telegram

Este documento describe la integración del proyecto **DRR APIs** (API de productos) con el bot de Telegram **Jarvis**, para consultar productos, buscar y gestionar imágenes desde DuckDuckGo, y opciones de mejora/creación de imágenes.

---

## 1. Contexto y objetivos

- **DRR APIs**: API REST (p. ej. Blazor/ASP.NET Core) que expone productos con código de barras, descripción y datos asociados.
- **Bot Telegram (Jarvis)**: Asistente en el servidor con comandos de voz, logs, búsqueda web (DuckDuckGo) y generación de imágenes (Gemini/OpenAI).
- **Objetivo**: Ofrecer desde Telegram una entrada única para:
  - Consultar uno o varios productos con filtros (descripción, código de barras, etc.).
  - Ver información básica y clara de cada producto.
  - Buscar imágenes por código de barras o descripción (DuckDuckGo).
  - Guardar, ver y mejorar/crear imágenes de producto.

Para **formato del listado, botones inline y log de diagnóstico**, ver [DRR_BOT_UI_LOGS.md](DRR_BOT_UI_LOGS.md).

---

## 2. Contrato esperado de la API de productos (DRR)

Para que el bot pueda integrarse con cualquier backend DRR, se asume el siguiente contrato mínimo.

### 2.1 Listado con filtros

- **Método**: `GET`
- **URL**: `{DRR_API_BASE_URL}/api/productos` (o equivalente)
- **Query params** (todos opcionales):
  - `descripcion`: fragmento de texto para filtrar por descripción.
  - `codigo_barras`: código de barras exacto o parcial.
  - `limit`: cantidad máxima de resultados (por defecto el servidor puede usar 20).

**Respuesta esperada** (JSON):

```json
[
  {
    "id": 1,
    "codigoBarras": "7891234567890",
    "descripcion": "Producto ejemplo",
    "imagenUrl": "https://...",
    "precio": 99.99,
    "stock": 10
  }
]
```

Los nombres de propiedades pueden variar (camelCase o snake_case); el cliente en el bot puede mapear ambos.

### 2.2 Detalle de un producto

- **Método**: `GET`
- **URL**: `{DRR_API_BASE_URL}/api/productos/{id}`

**Respuesta**: mismo objeto que un elemento del listado.

### 2.3 Actualización de imagen (opcional)

Si la API DRR permite actualizar la imagen del producto:

- **Método**: `PUT` o `PATCH`
- **URL**: `{DRR_API_BASE_URL}/api/productos/{id}` o `.../api/productos/{id}/imagen`
- **Body**: según diseño (multipart, URL, base64, etc.).

Si no está disponible, el bot puede guardar imágenes solo en local (carpeta `productos_imagenes/` o similar) y documentarlo.

---

## 3. Variables de entorno (servidor)

En el `.env` del bot:

| Variable | Descripción |
|----------|-------------|
| `DRR_API_BASE_URL` | URL base de la API de productos (ej: `https://tu-servidor.com/api` o `http://localhost:5000`). Si está vacía, los comandos de productos informan que no está configurada. |
| `DRR_API_KEY` | (Opcional) Token directo si tu DRR ya expone un token “final” listo para usar. Si está definido, **se usa antes** que el flujo por etapas. |

### 3.1 Flujo de tokens por defecto (TOKEN CLIENTE → TOKEN DEV → TOKEN USER → TOKEN FINAL)

Si **NO** definís `DRR_API_KEY`, el bot puede intentar automáticamente un flujo por etapas, alineado con el Swagger de DRR:

1. **TOKEN CLIENTE** → se usa como Bearer para llamar a `POST /Auth/TokenDeveloper` y obtener el **TOKEN DEV**.  
2. **TOKEN DEV** → se usa como Bearer para llamar a `POST /Auth/TokenUser` junto con `user` y `pwd` y obtener el **TOKEN USER**.  
3. **TOKEN FINAL** → se arma concatenando ambos: `TOKEN_DEV.TOKEN_USER` (con un punto en el medio). Este es el que después se usa como `Authorization: Bearer TOKEN_FINAL` para las demás APIs (incluyendo `/Producto`).  

Variables necesarias en `.env` para que el bot pueda hacer ese flujo por sí solo:

| Variable | Descripción |
|----------|-------------|
| `DRR_CLIENT_TOKEN` | **TOKEN CLIENTE** (primer eslabón; el que apunta a la empresa, por ejemplo “Cursos”). |
| `DRR_DEV_USER` | Usuario (para token usuario). |
| `DRR_DEV_PASSWORD` | Contraseña (para token usuario). |
| `DRR_AUTH_DEV_PATH` | (Opcional) Path para obtener token dev. Default: `/Auth/TokenDeveloper`. |
| `DRR_AUTH_USER_PATH` | (Opcional) Path para obtener token usuario. Default: `/Auth/TokenUser`. |

Notas:
- **Seguridad**: estas credenciales van en `.env` (no en el código, ni en el repo).
- **Contrato Swagger**: el Bearer “final” para DRR se arma como `TokenDev.TokenUser` (concatenación con punto). Ejemplo real: `4FE2...C9.6B08...B4` (NO commitear tus GUID reales).
- **Paths**: si tus endpoints reales se llaman distinto (ej. `/api/Auth/...`), ajustá los `*_PATH`.

### 3.1.1 Usar directamente un TOKEN FINAL ya generado

Si ya tenés un **TOKEN FINAL** válido (por ejemplo, algo como `4FE2...C9.6B08...B4`) y preferís **no** que el bot haga el flujo cliente→dev→user cada vez:

- Configurá solo:

```env
DRR_API_BASE_URL=https://drrsystemas4.azurewebsites.net
DRR_API_KEY=TU_TOKEN_FINAL_AQUI   # exacto, con el punto en el medio
```

- En ese caso:
  - El cliente `DRRProductoAPIClient` usará siempre `Authorization: Bearer DRR_API_KEY`.
  - El módulo de autenticación (`drr/auth.py`) queda disponible pero no se utiliza.

---

## 3.2 Autenticación alternativa

Si la API usa autenticación distinta (API key fija, JWT directo, etc.), podés usar solo `DRR_API_KEY` y omitir el flujo por etapas.

---

## 4. Comandos de Telegram: DRR / Productos

Todos los comandos requieren sesión activa (`/login`) y chat autorizado.

### 4.1 `/productos` — Menú y ayuda

- **Uso**: `/productos` o `/productos ayuda`
- **Descripción**: Muestra un resumen de subcomandos y ejemplos.

### 4.2 Listar productos con filtro

- **Uso**: `/productos listar descripcion <texto>` | `/productos listar codigo <código>` | `/productos listar todo`
- **Descripción**: Obtiene de la API DRR uno o varios productos y los muestra en formato breve (código, descripción, si tiene imagen).

### 4.3 Ver detalle de un producto

- **Uso**: `/productos ver <id o código de barras>`
- **Descripción**: Muestra la información completa del producto y, si existe, la imagen actual (thumbnail o enlace).

### 4.4 Buscar imagen (DuckDuckGo)

- **Uso**: `/productos imagen buscar <código o descripción>`
- **Descripción**: Busca imágenes en DuckDuckGo usando código de barras y/o descripción del producto, y envía la primera (o varias) como foto en el chat.

### 4.5 Guardar imagen

- **Uso**: `/productos imagen guardar <id o código> [índice]`
- **Descripción**: Toma la última imagen buscada para ese producto (o la del índice indicado) y la guarda en local y/o la envía a la API si está definido el endpoint de actualización.

### 4.6 Mejorar o crear imagen (IA)

- **Uso**: `/productos imagen mejorar <id o código> [descripción opcional]`
- **Descripción**: Genera o mejora la imagen del producto con IA (Gemini/OpenAI) a partir de la descripción del producto (o la descripción adicional que se pase) y la envía al chat; opcionalmente la guarda.

### 4.7 Ver imagen actual del producto

- **Uso**: `/productos imagen ver <id o código>`
- **Descripción**: Muestra la imagen actual asociada al producto (desde API o desde carpeta local si se guardó ahí).

---

## 5. Estructura del código (SOLID)

- **Interfaces (D)**: `IProductoRepository`, `IBuscadorImagenes`, `IAlmacenImagenes` — el bot depende de abstracciones, no del cliente HTTP ni de DuckDuckGo directo.
- **Cliente API (S/I)**: Un solo responsable por “hablar con la API DRR”; implementa `IProductoRepository`.
- **Búsqueda de imágenes (S)**: Clase que encapsula DuckDuckGo e implementa `IBuscadorImagenes`.
- **Formateo (S)**: Clase que convierte `Producto` a texto/HTML para Telegram (una responsabilidad).
- **Servicio de aplicación (O)**: Orquesta repositorio, buscador de imágenes y formateador; se puede extender con nuevas fuentes de imagen o almacenes sin cambiar comandos del bot.
- **Comentarios**: En cada módulo, comentarios útiles que expliquen el “por qué” del contrato y de los parámetros.

---

## 6. Ideas y extensiones futuras

- **Favoritos / recientes**: Guardar en contexto (o en un pequeño store) los últimos productos consultados para poder decir “traé el último” o “el del código 123”.
- **Búsqueda por voz**: Que un mensaje de voz como “producto leche entera” se parsee y se llame a listar con filtro descripción “leche entera”.
- **Exportar**: Comando para exportar lista de productos (o un producto) a nota o archivo (MD/CSV) y guardarlo en el log o en Nextcloud.
- **Alertas**: (Si la API lo soporta) notificar cuando stock bajo o cuando un producto no tiene imagen.
- **Múltiples imágenes por producto**: Si el modelo de la API lo permite, listar y elegir una de varias imágenes para “guardar” o “mejorar”.
- **Caché local**: Cachear respuestas de la API por unos minutos para no saturar en consultas repetidas.
- **Límite de resultados**: En listado, límite configurable (ej. 5 por defecto) y opción “ver más” con paginación o siguiente página.

---

## 7. Migración desde el proyecto Blazor/API

- Mantener este MD en el servidor (por ejemplo `telegram-bot/docs/`) como referencia única para la integración Telegram + DRR.
- Si tenés un `DOCUMENTACION_MIGRACION_DRR.md` en el proyecto Windows (BlazorApp_ProductosAPIcopia), podés:
  - **Copiar aquí** las secciones específicas de la API (endpoints exactos, ejemplos de JSON) y reemplazar la sección 2 con esos datos.
  - **Fusionar** ambos documentos: en el repo del servidor dejar “Integración Telegram” (este doc) y en el repo Blazor dejar “Migración backend / despliegue”.
  - **Pegar el contenido** del MD de tu PC en este archivo y ajustar solo la sección de comandos Telegram si ya existe.
- Asegurar CORS en la API DRR si el bot corre en otro dominio; si el bot solo llama desde el servidor (backend), CORS no aplica pero sí la URL y eventual autenticación.
- **Rutas del cliente**: Si `DRR_API_BASE_URL` termina en `/api` (ej. `https://host.com/api`), el cliente pide `GET /productos` y `GET /productos/{id}`. Si no, usa `GET /api/productos`.

---

## 8. Referencia: repo ProductosAPI (Blazor)

Repositorio: [RossnagelRuben/ProductosAPI](https://github.com/RossnagelRuben/ProductosAPI).

### 8.1 Cómo busca productos la app Blazor

- **Servicio:** `Services/AsignarImagenes/ProductoQueryService.cs`
- **Endpoint:** `GET https://drrsystemas4.azurewebsites.net/Producto/GetProducto` (no `GET /Producto`).
- **Query params usados:**
  - `pageSize`, `pageNumber` (paginación)
  - `Imagen=true`, `Include=2` (incluir imagen y observaciones)
  - `codigoBarra` — filtro por código de barras (opcional)
  - `descripcionLarga` — filtro por descripción (opcional)
  - `familiaID`, `marcaID`, `sucursalID`, `fechaModifDesde`, `fechaModifHasta`
  - `ConCodigoBarra` — true/false para “con código” / “sin código”
- **Respuesta:** JSON con propiedad `data` (array de productos). Cada item puede tener `codigoID`, `codigoFabrica`, `descripcionLarga`, `codigoBarra`, `presentaciones`, imagen en distintas propiedades anidadas.

**Importante para el bot:** Si `GET /Producto` con `Search` devuelve 0 resultados, probar `GET /Producto/GetProducto` con `descripcionLarga` y `codigoBarra` (y `pageSize`, `pageNumber`), y parsear `response.data`.

### 8.2 Cómo asigna imágenes la app Blazor

- **Pantalla:** `Pages/AsignarImagenes.razor` + code-behind `AsignarImagenes.razor.cs`
- **Filtros:** Descripción, código de barra, familia, marca, “con/sin imagen”, “con/sin código”, “con/sin observación”. La búsqueda llama a `ProductoQuery.GetProductosAsync(filter, token)`.
- **Texto para buscar imagen:** Se arma con `ImageSearchQueryHelper`:
  - **Con código de barra:** `"[código de barra] [descripción]"` (ej. `7791337601024 YOGURISIMO C/FRUTAS...`).
  - **Sin código:** solo descripción.
  - Query inicial corta: código de barra + primeras N palabras de la descripción (ej. 2 palabras) para no enviar texto muy largo a la API.
- **Fuentes de imágenes:** Google Custom Search (varios API keys en rotación), SerpAPI, o API propia `/Integration/ImageSearch`. Ver `README GOOGLE BUSQUEDAS.md` en el repo para Google (parámetros `key`, `cx`, `q`, `searchType=image`, `fileType=jpeg,png`).
- **Guardado en DRR:** Al elegir una imagen se envía **PATCH** al producto:
  - **Servicio:** `Services/AsignarImagenes/ProductoPatchService.cs`
  - **URL:** `PATCH https://drrsystemas4.azurewebsites.net/Producto`
  - **Body (JSON):** `codigoID`, `imagenEspecified=true`, `imagen` (string; suele ser base64 o URL según API), y opcionalmente `observacionEspecified`, `observacion`, `descripcionCortaEspecified`, `descripcionCorta`, `descripcionLargaEspecified`, `descripcionLarga`.

Resumen: la app Blazor usa **GetProducto** para listar, **código de barra + descripción** para la query de imagen, y **PATCH /Producto** con `imagenEspecified` e `imagen` para asignar la imagen.

### 8.3 Documentación adicional en el repo

- `README GOOGLE BUSQUEDAS.md` — Búsqueda de imágenes con Google Custom Search (URL, parámetros, tokens, formato de `q` con código de barras y descripción).
- `ImageSearchQueryHelper.cs` — Construcción de queries y variantes/fallbacks para búsqueda de imágenes.

---

## 9. Changelog

| Fecha       | Cambio |
|------------|--------|
| 2026-03-14 | Documento inicial en el servidor: contrato API, comandos Telegram, SOLID, ideas y migración. |
| 2026-03-14 | Sección 8: referencia a repo ProductosAPI (Blazor) — cómo busca productos (GetProducto), cómo asigna imágenes (query código+descripción, PATCH /Producto), ImageSearchQueryHelper, README GOOGLE BUSQUEDAS. |
