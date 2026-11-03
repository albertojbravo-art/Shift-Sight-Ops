# Capa de intercambios — procedencia de los textos

Los textos de `exchanges.json` tienen **una sola fuente de verdad**:
`guion/respuestas-reales-copiloto.md` (fuera de este repo, en la carpeta
del proyecto), la corrida **en vivo por Baseten** del 2026-11-02 con el
paquete `copilot/` — modelo `meta-models/muse-spark-open-1026`, sesión
única y continua. **El modo degradado NO se usó**: respondió el modelo,
llamando a las herramientas del motor.

- **Intercambio 1 (min 0):** pregunta exacta «Why are we at risk if not a
  single box has moved?» → llamadas reales `preshift_projection` y
  `decision_ladder` (at_minute 0): 94.0%, nodo límite Picking, 5
  movimientos → 99.3%, sin posteo.
- **Intercambio 2 (min 165):** la línea hablada es «What do I do?»; el
  mensaje enviado añade el contexto que el tablero muestra en ese
  instante (parada de las 20:00, alerta de no-escaneo) — el mismo patrón
  de la tercera pregunta canónica del CLI del paquete. Llamadas reales
  `live_projection` (165) y `decision_ladder` (165): 73.1%, parada
  Picking → Induct, 4 movimientos → 76.2%, posteo redactado Picking
  5 personas × 5 horas.

Transparencia de la corrida (del archivo canónico): hubo dos corridas;
en la primera la respuesta 2 salió truncada porque el tope por defecto
del paquete (1024 tokens) lo consume el razonamiento del modelo. La
segunda subió el presupuesto a 3072 **solo en la capa de transporte**;
no se tocó ninguna pregunta, ni el prompt del sistema, ni las
herramientas. Las respuestas de `exchanges.json` son, completas y
verbatim, las de la segunda corrida.

**Qué muestra la tarjeta:** el campo `phrases` — los resúmenes del
guion `GUION-DEMO.md`, marcados **[acortada]**: la respuesta real
resumida a ritmo de lectura, sin cambiar ninguna cifra ni el sentido,
para que el video y el tablero digan lo mismo. La respuesta completa
verbatim queda en el campo `answer` de cada intercambio. Las fichas de
la tarjeta (`engine · <herramienta> → <valor>`) salen de las llamadas
reales listadas arriba; el valor es la cifra titular del resultado de
cada herramienta.
