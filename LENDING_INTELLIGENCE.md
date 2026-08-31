# Lending Intelligence — v1

La implementación usa únicamente Kamino Lend en Solana, persiste snapshots
normalizados, histórico de APY y una evaluación explicable de oportunidad.
No calcula Risk Score ni recomendaciones automáticas.

## Fuente investigada

Se usa la REST API oficial de Kamino, cuya documentación publica el catálogo
`/v2/kamino-market` y las métricas por reserva en
`/kamino-market/{market_id}/reserves/metrics`. La URL base es configurable con
`KAMINO_API_BASE_URL`; no se depende de un ID de mercado fijo. El SDK oficial
es TypeScript y requiere RPC, por lo que no es necesario para esta primera
ingesta read-only.

En la respuesta real observada el 2026-08-30 se encontraron:

- `reserve`, `liquidityToken`, `liquidityTokenMint`
- `supplyApy`, `borrowApy`
- `totalSupplyUsd`, `totalBorrowUsd`

`utilization` y `availableLiquidityUsd` no llegaron en la respuesta consumida.
No se calculan a partir de supply/borrow: se persisten como `NULL` y se anotan
en `missing_fields`. Esto queda pendiente de una investigación específica de
la semántica on-chain/API.

## Comando

Una ejecución:

```text
python fetch_lending_markets.py --asset USDC --asset PYUSD --asset SOL
```

Ejecución periódica cada 15 minutos:

```text
python fetch_lending_markets.py --interval 900
```

Las tablas `lending_markets` y `lending_snapshots` se crean de forma aditiva
al iniciar `Database`; el snapshot es único por protocolo, chain, mercado,
reserva y timestamp UTC.

## Auditoría de la segunda etapa

Dos ejecuciones reales consecutivas encontraron 38 markets y 238 reservas por
ejecución; quedaron 476 snapshots históricos en la base. El rango observado de
`supply_apy` fue `0.0` a `1.6006406906` (0% a aproximadamente 160.06%). Los
casos extremos se conservan si son finitos y están dentro del rango técnico
aceptado, pero se marcan en `quality_flags` como anómalos para no confundirlos
con un ranking.

La documentación oficial muestra `supplyApy: "0.038..."` y la guía convierte
ese valor a porcentaje multiplicando por 100; esto confirma la convención
decimal. También documenta que el SDK calcula utilization desde el estado de
la reserva (`calculateUtilizationRatio()`). Por lo tanto, utilization no se
debe inferir a ciegas desde una respuesta REST que no lo trae.

La investigación actual concluye:

- `utilization`: no está en la respuesta REST de métricas observada; sí está
  disponible como cálculo del SDK/on-chain sobre la reserva. No se implementa
  aún para mantener separadas observación y derivación.
- `available liquidity`: la API REST de métricas no lo entrega. La
  documentación del SDK/Rust apunta a `reserve.liquidity.available_amount`,
  pero la liquidez realmente disponible puede tener restricciones adicionales
  (por ejemplo, colas de retiro en modelos de vault). No se sustituye por
  `totalSupplyUsd - totalBorrowUsd`.

La inspección de snapshots se puede hacer con:

```text
python fetch_lending_markets.py --show-latest --asset USDC --protocol kamino --limit 20
```

Para activar el enrichment on-chain/SDK durante una ingesta:

```text
python fetch_lending_markets.py --with-sdk-enrichment --asset USDC --asset SOL
```

Si el RPC no está disponible, los snapshots REST se conservan y el fallo del
enrichment se registra; no se rellena utilization con una aproximación.

La carga SDK usa `KaminoMarket.loadMultiple` en una sola invocación Node/RPC,
en lugar de iniciar un proceso por market. En una ejecución real posterior, el
perfil fue: REST 5.01s, DB 0.07s, SDK/RPC 8.13s, total 13.22s; 240/240 reserves
enriquecidas correctamente. `available_amount_native` representa la cantidad
de tokens en unidades mínimas (`totalAvailableAmount`) y todavía no se
convierte a USD.

## Opportunity Score v1

Identidad persistida: `score_model=lending_opportunity`,
`score_version=1.0`. Las evaluaciones conservan además los puntos brutos
disponibles, el peso disponible y el peso faltante, de modo que una futura
versión no reinterprete retrospectivamente los scores anteriores.

Comandos:

```text
python fetch_lending_markets.py --rank --asset USDC --protocol kamino --limit 10
python fetch_lending_markets.py --explain <reserve_id> --asset USDC --protocol kamino
```

La elegibilidad exige IDs válidos, `supply_apy` finito/no negativo y
`total_supplied_usd` positivo. Un APY extremo pero finito se conserva como
anómalo válido en `quality_flags`; valores ausentes o inválidos no reciben
score. La relevancia económica clasifica el suministro como `micro` (<$10k),
`small` ($10k–<$100k), `medium` ($100k–<$1M) o `large` (≥$1M).

El score 0–100 combina componentes normalizados a 0–1:

| Componente | Peso | Señal |
| --- | ---: | --- |
| yield_quality | 30% | APY actual, mediana filtrada 7d y cobertura |
| apy_persistence | 20% | cercanía del APY actual a la mediana 7d |
| apy_stability | 15% | dispersión MAD relativa de APY no anómalo |
| capacity | 15% | escala logarítmica de `total_supplied_usd` |
| utilization_health | 10% | bandas piecewise; penaliza >85% y estrés >95% |
| borrow_demand | 10% | borrow APY y utilización disponible |

La capacidad usa una escala logarítmica entre $1k y $100M. No se inventa
`available_liquidity` ni se sustituye por `supplied - borrowed`; en v1 la
capacidad usa principalmente `total_supplied_usd`, mientras la liquidez
nativa del SDK queda como señal observada para una etapa posterior.

Cada evaluación guarda `history_status` para 24h, 7d y 30d: `insufficient`
(<25% de cobertura), `partial` (25%–<90%) o `complete` (≥90%). La confianza
combina cobertura 7d, muestras y disponibilidad de persistencia/estabilidad;
por eso una ejecución reciente puede tener score, pero queda marcada como
`low_confidence` e `insufficient_history`.

### Corrección de disponibilidad histórica

Cuando `history_status_7d` es `insufficient`, `apy_persistence` y
`apy_stability` quedan en `NULL` con estado `unavailable`; no reciben puntos.
`yield_quality` usa únicamente el APY actual y no rellena la mediana 7d.

En ese estado se calcula `provisional_opportunity_score`, normalizado sobre
los pesos disponibles (65% con la configuración actual), y se persisten
`available_weight=0.65` y `missing_weight=0.35`. El score histórico definitivo
queda separado en `opportunity_score` y sólo se habilita cuando todos sus
componentes están disponibles. Los estados son `PROVISIONAL`,
`PARTIAL_HISTORY` y `MATURE`.

También se expone `borrow_demand_without_utilization` como variante de
auditoría. No reemplaza la fórmula vigente: permite medir por separado el
efecto del 30% de utilization incluido actualmente dentro de borrow demand.

## Save / Solend

Save se integra mediante un adapter independiente en
`services/lending/save.py`. El discovery usa el catálogo oficial
`/v1/markets?scope=all`, la configuración de cada market mediante
`/v1/markets/configs?ids=...`, estado de reserves por `/v1/reserves?ids=...`
y precios por mint mediante `/v1/prices?mints=...`.

La respuesta real observada contiene 202 markets y, para USDC/SOL, 192
reserves normalizadas. `availableAmount` y `borrowedAmountWads` se conservan
con sus unidades y decimales; utilization se deriva dentro del adapter como
borrowed / (available + borrowed), con ambos valores del mismo payload. Supply
APY y borrow APY se toman de `rates.supplyInterest` y
`rates.borrowInterest`, convertidos de porcentaje API a decimal interno.

Los valores USD usan el precio contemporáneo de la API oficial de precios.
Cuando el precio falta, se conserva la liquidez nativa y se registran los
campos USD como missing; nunca se usa `supplied - borrowed` como sustituto.
El score v1 no incorpora diferencias de seguridad entre Kamino y Save: la
capa de Protocol Risk queda pendiente.
