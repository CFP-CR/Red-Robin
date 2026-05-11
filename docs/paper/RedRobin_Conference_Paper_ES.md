# Red Robin: No Hackees Por Mí, Hackea Conmigo
## Un Compañero de IA en Tiempo Real para Operadores de Red Team

**Propuesta enviada a:** La Villa @ DEF CON 34 (fecha límite 2026-06-01)
**Track:** Red Team & Seguridad Ofensiva
**Formato:** Presentación + Demo en Vivo (45 min)
**Estado:** Implementación v0.1 completa; laboratorio de demo provisionado

---

## Resumen (200-300 palabras)

La ola de 2025-2026 de "herramientas de IA para hacking" — Villager, HexStrike,
PentAGI, servidores Cobalt-Strike-MCP públicos conectados a LLMs de frontera —
ya zanjó la cuestión del piloto automático para cualquier red teamer serio:
fracasa. La demostración pública de b-ops.be lo capturó con precisión: entregue
un Beacon recién obtenido y un servidor CS-MCP a un LLM, y lo primero que
propone es `mimikatz logonpasswords` — un comportamiento que terminaría la
mayoría de los engagements reales en menos de un minuto. Estas herramientas
no tienen concepto de detection priors, no tienen concepto del alcance del
engagement, no tienen concepto de la shell que el operador acaba de ganar tras
una semana de trabajo.

Red Robin propone el modelo opuesto: la IA como **un coach al que el operador
puede negarle la sugerencia**, no como un agente que tipea por él. Utiliza el
mecanismo `alias` de Aggressor de Cobalt Strike — la misma primitiva
production-proven en la que las OPSEC Profiles de SpecterOps confían desde
2018 — para interceptar comandos peligrosos de Beacon en el CS Client del
operador *antes* de que lleguen al teamserver. Un protocolo de veredicto de
dos niveles devuelve decisiones deterministas de Layer A (reglas YAML) en
menos de 150ms (gating sincrónico production-grade), mientras que un juez
Layer B (LLM) con contexto recuperado de la knowledge base vía RAG corre de
forma asíncrona para patrones novedosos, publicando advertencias retroactivas
en el TUI del operador. Cinco capas de memoria (in-engagement, cross-engagement,
operator-personal, KB y Tradecraft Graph) hacen que los veredictos sean
conscientes del engagement, no genéricos.

La presentación demostrará Red Robin en vivo contra un despliegue de Cobalt
Strike + GOAD-Light, incluyendo el bloque DANGER que atrapa `vssadmin delete
shadows` en vuelo (Escena 7 del demo runbook) y el chat de operador consciente
del engagement (Escena 8). Seremos honestos sobre lo que Red Robin *no* hace —
incluyendo el alcance per-operator del gate (T11) que emerge del modelo de
scripting client-side de Cobalt Strike — y lo que v0.2 va a resolver.

## Descripción (600-800 palabras)

### Por qué las herramientas actuales de IA para hacking fracasan en engagements reales

El mercado de "IA para seguridad ofensiva" en 2025-2026 se dividió en dos
campos: **pilotos automáticos** que intentan conducir el engagement (Villager,
HexStrike, PentAGI, demos exploratorias de CS-MCP-server-más-Claude) y
**asistentes** conectados a IDEs o ventanas de chat (Burp Copilot, Caido AI).
Ambos pierden lo que los operadores senior realmente quieren de un co-piloto
de IA en un engagement real.

El investigador de b-ops.be demostró el modo de fallo del piloto automático
públicamente en 2025: se expone un servidor MCP de Cobalt Strike a Claude
Sonnet 4.5, post-recon se le pregunta "¿cuál es el mejor próximo paso?" —
y el modelo recomienda con confianza `mimikatz logonpasswords`, con la sintaxis
completa del comando Aggressor. El modelo no tiene consciencia de que un EDR
lo atrapará, no tiene consciencia de si el operador ya pivotó, no tiene
consciencia de que LSASS está en PPL en Windows moderno. Los pilotos automáticos
están leyendo la sala como un intern con suscripción a Hack The Box.

Mientras tanto los asistentes atados al IDE son inútiles en un engagement vivo
porque no tienen acceso al estado del engagement — sin output del Beacon, sin
notas del operador, sin conocimiento de lo que el equipo está intentando
lograr esta semana.

### Arquitectura de Red Robin (pipeline de veredicto de 4 etapas + memoria de 5 capas + adapter pattern)

Red Robin se sitúa entre el operador y el C2. **No** es un reemplazo del
cerebro del operador — es un cerebro extra que atrapa los errores de alta
señal y responde las preguntas de alta señal, mientras deja el trabajo rutinario
sin intervención.

La arquitectura se construye sobre cuatro pilares:

1. **Adapter pattern.** Un wire protocol agnóstico al C2 (v0.1) nos permite
   añadir soporte para cualquier framework C2. v0.1 envía el adaptador de
   Cobalt Strike (Aggressor + puente Python REST) y un adaptador Sliver-Mock
   para trabajo de demo offline. Los adaptadores Sliver-Real y Mythic están
   en el roadmap v0.1.1 / v0.2.

2. **Protocolo de veredicto de dos niveles.** Layer A es un motor determinista
   de reglas YAML que devuelve veredictos ALLOW / WARN / BLOCK / NEEDS_REVIEW
   en menos de 150ms p95 — lo suficientemente rápido para gating sincrónico
   del comando del operador. Layer B es un juez LLM asíncrono respaldado por
   RAG sobre embeddings de Voyage de la knowledge base de Red Robin (reglas
   Sigma, detection priors de ATT&CK, OPSEC profiles de SpecterOps, notas de
   tradecraft del operador). Layer B publica veredictos WARN retroactivos en
   el TUI para patrones novedosos que Layer A no atrapó.

3. **Memoria de 5 capas.** Estado in-engagement (Beacons actuales, comandos
   recientes, pivots), aprendizaje cross-engagement (qué funcionó / falló en
   targets similares), preferencias operator-personal (tu tradecraft favorito),
   la knowledge base, y la Tradecraft Graph (un grafo normalizado de técnicas
   y sus perfiles de detección). El TUI muestra memoria relevante en el momento
   de la decisión.

4. **Aggressor alias gate.** La pieza técnica central. Las OPSEC Profiles de
   SpecterOps, en producción desde 2018, demostraron que el mecanismo `alias`
   de Aggressor puede interceptar de forma transparente comandos de Beacon en
   el CS Client del operador. Red Robin usa la misma primitiva — 28 hooks de
   alias en `redrobin.cna` cubriendo los comandos más abusados (mimikatz,
   logonpasswords, sekurlsa, vssadmin, shadow copies, extracción NTDS,
   Kerberos roasts, lateral movement, etc.). Cuando el operador tipea
   `mimikatz logonpasswords` en la consola del Beacon, el alias dispara el
   daemon, el daemon devuelve un veredicto en <150ms, y el comando o se
   despacha (con anotación) o se bloquea antes de llegar al teamserver.

### Reconocimiento honesto del alcance per-operator (T11)

No vamos a vender esto de más. El alias gate está acotado a los operadores
que cargan el script `redrobin.cna`. Un operador en el mismo teamserver que
no haya cargado el script evita el gate. **Esto es una propiedad arquitectónica
del modelo de scripting client-side de Cobalt Strike, no una decisión de
diseño de Red Robin — y lo documentamos abiertamente.** El hook nativo
`opsec_pre` de Mythic es server-side y evita esto por completo, lo cual es
por qué estamos construyendo el adaptador Mythic para v0.2. El SOP del equipo
recomienda que todos los operadores en un engagement carguen el script; el
daemon también publica un evento de gate-presence per-operator al TUI para
que el team lead pueda ver quién está y quién no está gated.

### Vista general de la demo en vivo (11 escenas según el demo runbook)

El talk demuestra contra un teamserver de Cobalt Strike + Beacon + laboratorio
AD GOAD-Light en vivo, en 11 escenas mapeadas a `docs/demo-runbook.md`.
Destacados:

- Escena 4: warm-path discovery (permitido, anotado).
- Escena 7: **bloqueo DANGER** — el operador intenta `vssadmin delete shadows
  /all`, Layer A lo atrapa, veredicto BLOCK, el comando nunca llega al
  teamserver.
- Escena 8: **chat de operador consciente del engagement** — el operador
  pregunta "¿cuál es el camino más limpio desde aquí?" y el juez LLM responde
  fundamentado en el estado actual del Beacon, los objetivos documentados del
  engagement y los detection priors de la Tradecraft Graph.

### Roadmap v0.1 vs. v0.2

v0.1 (presentado en DEF CON 34) envía: adaptador CS, adaptador Sliver-Mock,
daemon, TUI, 28 hooks de alias, motor de reglas Layer A, juez LLM Layer B,
memoria de 5 capas, recuperación KB-RAG, demo end-to-end. v0.1.1 (objetivo
22 de junio): adaptador Sliver-Real. v0.2 (finales de 2026): modo local (sin
egress de API), adaptador nativo Mythic `opsec_pre`, expansiones de Tradecraft
Graph, coordinación multi-operator de equipo.

## Outline (numerado, ~5-7 secciones)

1. **El panorama actual: hombre vs. máquina** (5 min)
   - Explosión de IA para hacking post-2025
   - Dónde fracasan: Villager, HexStrike, CS-MCP-server + Claude Sonnet 4.5
   - El ejemplo de Mimikatz de b-ops.be
   - Por qué los operadores no confían en el piloto automático

2. **Cómo se ve el red teaming real** (5 min)
   - La shell que no te puedes permitir perder
   - Decisiones adaptativas bajo presión de tiempo
   - El caso a favor de la aumentación sobre la automatización

3. **Presentando Red Robin** (5 min)
   - Origen: frustración con las herramientas existentes
   - Filosofía central: no hackees por mí, hackea conmigo
   - Arquitectura de alto nivel (un solo slide)

4. **Deep-dive técnico** (10 min)
   - Wire protocol v0.1 (agnóstico al adaptador)
   - Mecanismo Aggressor alias (primitiva production-proven)
   - Protocolo de veredicto de dos niveles (Layer A YAML <150ms + Layer B LLM async)
   - Memoria de 5 capas + aprendizaje cross-engagement
   - Discusión honesta del alcance per-operator (T11)

5. **Demostración en vivo** (15 min)
   - 11 escenas según el demo runbook
   - Pieza central del bloqueo DANGER (Escena 7 — vssadmin bloqueado)
   - Chat de operador con razonamiento consciente del engagement (Escena 8)

6. **Red Robin vs. el toolset existente** (3 min)
   - Lado-a-lado: Red Robin vs. herramientas BAS vs. scanners autónomos
   - Lo que Red Robin intencionalmente NO hace — y por qué eso es una feature

7. **El futuro de la IA para red teamers** (2 min)
   - Roadmap v0.2: modo local, adaptador Mythic, Tradecraft Graph
   - Preguntas abiertas para la comunidad

## Información del speaker

El presentador es un operador activo de red team con experiencia de campo en
entornos enterprise y engagements de simulación de adversarios. La trayectoria
abarca desarrollo de frameworks C2 (infraestructura de Sliver y Cobalt Strike),
desarrollo de malware con trabajo documentado de bypass de EDR, OSINT, y la
integración práctica de tooling de IA en operaciones ofensivas en vivo.
Actualmente cursando la certificación CRTO (activa) y operando un laboratorio
de red team manejado con Terraform/Ansible (GOAD-Light + Cobalt Strike) para
investigación continua. Las contribuciones previas a la comunidad incluyen
tooling open-source alrededor de automatización de infraestructura de red team.
Red Robin es el primer talk público del speaker específicamente sobre tooling
de IA; el release open-source acompañará la presentación. El link al repositorio
GitHub se proveerá en el envío.

## Puntos clave para los asistentes

- Una evaluación clara y honesta de lo que las herramientas actuales de IA para hacking pueden y no pueden hacer
- Un nuevo modelo mental para la IA como compañero operacional en vez de agente autónomo
- Familiaridad práctica con el TUI de Red Robin y su output de veredicto
- Insight práctico sobre cómo integrar asistencia de IA en un workflow de red team real
- Un framework para evaluar herramientas de IA futuras: ¿reemplaza el criterio o lo apoya?
- Release open-source previo a la presentación

## Fuentes y prior art

- Documento de spec: `docs/architecture-design.md` (~2600 líneas)
- OPSEC Profiles de SpecterOps (prior art del alias gate, 2018+)
- Hook nativo Mythic `opsec_pre` (SpecterOps, 2023+)
- b-ops.be "Cobalt Strike, MCPs, and the Illusion of Push-Button Intrusions" (2025+)
- Mapeos de detección de MITRE ATT&CK: T1003.001 (dump de LSASS), T1003.006
  (DCSync), T1490 (borrado de shadow copies), T1558.003 (Kerberoasting), entre otros

## Pre-requisitos para la demo

- Slot en el schedule de DEF CON 34 de al menos 45 minutos
- Wifi estable (egress a Anthropic API + Voyage API) — hotspot personal como backup
- Proyector 4K con texto legible desde el fondo de la sala
- Laptop del operador + laptop de backup (ambas pre-configuradas con daemon + TUI + CS Client)
- Engagement Acme Corp pre-grabado como escenario Sliver-Mock (backup ante fallo de red)
- Archivo de respuesta de Anthropic pre-grabado para la Escena 8 (backup ante rate-limit)
