# Crypto Radar LP Radar - Soak Test Report

## Scope

Soak de la tubería Raydium + Orca + Meteora con el scanner existente. Durante
este período no se modificaron Opportunity Score, hard filters, Asset Risk,
Volatility Risk formulas ni los pesos de Liquidity Structure Risk. No se borró
SQLite ni se generó backfill artificial.

## Período y ejecución

- Inicio observado en el log: `2026-08-30T15:07:43+00:00`
- Fin observado en el log: `2026-08-31T11:22:00+00:00`
- Duración aproximada: `20 h 14 min`
- Ciclos: `68`
- Frecuencia configurada: `15 minutos`
- Providers: Raydium, Orca y Meteora
- Ciclos finalizados: `68/68`
- Ciclos con `EXIT 0`: `68`
- Ciclos con `EXIT 1`: `0`
- Snapshots persistidos durante el runner: `794`

## Estabilidad

El soak no mostró crashes, tracebacks ni ciclos fallidos. Todos los ciclos
terminaron correctamente. No reapareció el bug anterior de providers marcados
como `LIVE` con cero pools: los ciclos live observados reportaron pools
analizados y oportunidades finales de forma consistente.

Durante el período se observó recuperación de red después del episodio previo
de `WinError 10013`. No se observaron snapshots económicos falsos producidos
por errores locales de red; los ciclos sin datos live terminaron con `EXIT 0`
y no avanzaron el histórico económico.

## Pipeline observado

La secuencia se mantuvo:

```text
raw -> normalized -> allowed -> pre_filter -> qualifying -> snapshot
```

Los contadores de cada provider permanecieron explicables por los hard
filters. En el último ciclo registrado:

- Raydium: `17,384` scanned, `5` qualifying.
- Orca: `10,022` scanned, `4` qualifying.
- Meteora: `1,850` scanned, `3` qualifying.

Los descartes quedaron separados por TVL, volumen, Volume/TVL y Fee APR. No se
completó artificialmente el ranking.

## Boundary churn: Meteora SOL/USDC

El pool `5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6` fue un caso real de
boundary churn alrededor del filtro de TVL de `$5M`:

- TVL observado: aproximadamente `$4.68M - $4.70M`.
- Volumen 24h: aproximadamente `$58M - $60M`.
- Volume/TVL: aproximadamente `12.5x`.
- Fee APR: aproximadamente `178% - 184%`.
- Motivo de descarte: `TVL below minimum`.
- Previous OPP registrado: `90.92`, rank `1`.

La oportunidad desapareció por el hard filter de TVL, no por falta de
actividad. El evento quedó registrado en `DROPPED SINCE LAST RUN`.

## Estado de cambios semánticos posteriores

Después del soak se corrigieron únicamente inconsistencias de presentación y
semántica:

- Whirlpool/CLMM sin distribución ya no muestran `Confidence: HIGH` cuando el
  score es `N/A`; muestran `N/A`.
- Volatility separa `metric_coverage_pct` de
  `window_coverage_24h_pct`, sin cambiar fórmulas ni thresholds.
- Se documenta el endpoint externo, ventana rolling de 30 días, parámetros
  `from`/`to` Unix, intervalo hourly, cache TTL y redondeo a buckets UTC.
- El merge mantiene deduplicación por bucket horario, prioriza la mediana de
  fuentes locales cuando existe y deja visibles los gaps.

## Conclusión

El soak de 68 ciclos fue estable y suficiente para continuar con la siguiente
fase después de estas correcciones semánticas. Scoring, hard filters y lógica
económica permanecen congelados.
