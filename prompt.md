# ROL Y OBJETIVO
Actúa como un Ingeniero de Software Senior especialista en Agentes de IA. Vamos a crear un agente de IA académico para alumnos de la UTN FRM cumpliendo estrictamente con las consignas de `Consigna Integrador.pdf` y siguiendo la arquitectura, estilo de código y formato del repositorio `https://github.com/fmaya02/AgenteInvestigadorVerFinal`.

La única función y salida final del agente es generar bancos de preguntas interactivos en formato JSON listos para cargarse en la plataforma **estud.ia** (`https://estud-ia-blond.vercel.app/`).

Antes de escribir código, inspecciona el documento `Consigna Integrador.pdf` y la estructura de los repositorios `https://github.com/fmaya02/AgenteInvestigadorVerFinal` y `https://github.com/ignaw05/campus-utn-frm-mcp`.

---

# 1. ARQUITECTURA DEL MODELO Y ORQUESTACIÓN
- **Proveedor y Modelo:** Utiliza un modelo gratuito de **OpenRouter** de la familia Llama con soporte nativo para *Tool Calling* (por defecto: `meta-llama/llama-3.3-70b-instruct:free`).
- **Patrón de Diseño:** Implementa obligatoriamente el **Patrón Adapter** para la capa del LLM, de modo que se pueda cambiar el modelo o el proveedor en el futuro sin modificar la lógica del agente ni de las herramientas.
- **Tipo de Agente (Tool Calling con jerarquía estricta):** El usuario ingresará un prompt indicando sobre qué **materia** y **temas** quiere sus preguntas (y opcionalmente la cantidad y los tipos de preguntas). El agente debe operar mediante *Tool Calling*, pero su *System Prompt* debe instruirlo estrictamente para seguir este orden de prioridad antes de generar las preguntas:
  1. **Primero:** Consultar siempre la herramienta del RAG de libros oficiales de la cátedra para obtener el respaldo teórico exacto.
  2. **Segundo:** Explorar el campus de la UTN FRM vía MCP para cruzar la teoría con el enfoque, ejercicios y recorte de temas de los docentes.
  3. **Tercero (Opcional):** Usar la herramienta de búsqueda en internet únicamente si queda algún concepto muy actual, herramienta técnica o ejemplo práctico que necesite refuerzo tras consultar el libro y el campus.
  4. **Cuarto (Cierre obligatorio):** Con todos los datos recopilados y limpios, invocar la herramienta generadora de práctica para estructurar, validar y devolver **únicamente** el JSON final de `estud.ia`.

---

# 2. HERRAMIENTAS (TOOLS) DEL AGENTE

Debes implementar y conectar las siguientes 4 herramientas:

### 1. RAG con los Libros Oficiales de la Materia (Fuente Principal)
- El sistema cuenta con una base de datos vectorial en **SQLite** donde están indexados los libros de cabecera de cada materia (según el programa oficial), donde **cada embedding representa exactamente dos (2) páginas del libro**.
- Cada vez que el alumno pide preguntas sobre una materia y tema, lo primero que hace el agente es verificar mediante esta tool si tiene cargada la bibliografía oficial de esa cátedra y buscar allí para asegurarse de que las definiciones, demostraciones y conceptos tengan el respaldo teórico exacto que piden los profesores.

### 2. Conexión al Campus de la UTN FRM (vía MCP)
- A través del servidor MCP conectado al campus Moodle (`https://github.com/ignaw05/campus-utn-frm-mcp`), el agente puede ver las materias y leer los archivos subidos por la cátedra (PDFs, presentaciones, cronogramas y guías).
- Esto le permite cruzar lo que dice el libro oficial con el enfoque particular, los ejercicios y el recorte de temas que dan los docentes en las clases.

### 3. Tool de Búsqueda en Internet (Gratuita y sin API Key)
- Implementada con una librería gratuita que no requiera API Key (como `duckduckgo-search` / `ddgs`).
- Si después de consultar el libro oficial en el RAG y revisar las filminas del campus todavía queda algún concepto muy actual, una herramienta técnica o un ejemplo práctico que necesite refuerzo, el agente usa esta herramienta para buscar en internet y completar el contexto sin salirse del enfoque de la cátedra.

### 4. Tool Generadora de Práctica para estud.ia (Salida Final)
- Es una herramienta dedicada que recibe el contenido limpio y recopilado de las fuentes anteriores (junto con los parámetros de cantidad y tipos de preguntas pedidos por el usuario; **por defecto 10 preguntas** si el usuario no especifica cantidad) y genera/valida el banco de preguntas para exportarlo a `https://estud-ia-blond.vercel.app/`.
- La salida final del agente hacia el usuario debe ser **exclusivamente el JSON** producido y validado por esta herramienta.

---

# 3. ESPECIFICACIÓN DEL FORMATO JSON PARA ESTUD.IA

La herramienta generadora de práctica y la salida final del agente deben cumplir estrictamente con el siguiente prompt y estructura de datos:

```text
Quiero que conviertas el material que te paso abajo en un banco de preguntas para estudiar, en formato JSON, para cargarlo en la página estud.ia.

Devolvé SOLO el JSON (sin texto antes ni después, sin comentarios), con esta estructura exacta:

{
  "titulo": "Nombre corto del test",
  "descripcion": "Descripción opcional",
  "preguntas": [ ... ]
}

Cada pregunta tiene "tipo", "consigna" y, opcionalmente, "explicacion" (se muestra después de responder). Los tipos posibles son:

1. "multiple_choice": una sola opción correcta.
   { "tipo": "multiple_choice", "consigna": "...", "opciones": ["A", "B", "C", "D"], "correctas": [1] }
   "correctas" tiene UN solo índice; los índices empiezan en 0 (la primera opción es 0).

2. "seleccion_multiple": una o más opciones correctas (el alumno tiene que marcar todas).
   { "tipo": "seleccion_multiple", "consigna": "...", "opciones": ["A", "B", "C", "D"], "correctas": [0, 2] }

3. "verdadero_falso":
   { "tipo": "verdadero_falso", "consigna": "Afirmación...", "respuesta": true }
   "respuesta" es true o false (booleano, sin comillas).

4. "completar": la consigna lleva uno o más huecos escritos como ___ (tres guiones bajos).
   { "tipo": "completar", "consigna": "La capital de Francia es ___.", "respuestas": [["París", "Paris"]] }
   "respuestas" tiene UNA lista por cada hueco, en orden, con todas las variantes aceptadas para ese hueco.

5. "texto": el alumno escribe la respuesta libremente (la corrige una IA comparándola con las respuestas correctas).
   { "tipo": "texto", "consigna": "...", "respuestas": ["Respuesta modelo", "Otra forma válida"] }

6. "unir": el alumno une con flechas cada elemento de la izquierda con su pareja de la derecha (de 2 a 10 pares).
   { "tipo": "unir", "consigna": "Uní cada país con su capital.", "pares": [["Francia", "París"], ["Italia", "Roma"], ["Japón", "Tokio"]] }
   Cada par es ["izquierda", "derecha"] y está correcto así: la página mezcla la columna derecha sola.

7. "ordenar": el alumno ordena pasos o hechos (de 2 a 12).
   { "tipo": "ordenar", "consigna": "Ordená las etapas de la mitosis.", "pasos": ["Profase", "Metafase", "Anafase", "Telofase"] }
   Escribí los "pasos" en el orden CORRECTO: la página los desordena sola.

Reglas:
- Usá comillas dobles, sin comas finales y sin comentarios: tiene que ser JSON válido.
- Mezclá los tipos de pregunta (salvo que el usuario pida un formato específico) y cubrí todos los temas importantes del material.
- Incluí siempre "explicacion" justificando la respuesta según el material de la cátedra.
- Las opciones incorrectas tienen que ser creíbles, no obvias.
- No repitas preguntas.
- Generá [CANTIDAD] preguntas (10 por defecto si el usuario no lo especifica).

Material:
[MATERIAL RECOPILADO DEL RAG DE LIBROS + CAMPUS UTN FRM + WEB]