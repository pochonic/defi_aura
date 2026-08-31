# Crypto Radar Solana — MVP

Scanner local de oportunidades LP de Raydium. Esta primera iteración solo hace discovery y monitoreo: no conecta wallets, no firma transacciones y no ejecuta operaciones.

## Ejecutar

```bash
python app.py
```

Opciones útiles:

```bash
python app.py --page-size 100 --max-pages 3
python -m unittest discover -s tests -v
```

La base `crypto_radar.db` se crea en el directorio de trabajo. Cada corrida guarda snapshots, auditoría de respuestas y alertas.

## API verificada

La fuente primaria es `GET https://api-v3.raydium.io/pools/info/list`, con `poolType=all`, paginación y orden por liquidez. Raydium documenta también `/pools/info/list-v2`, pero durante la validación de este MVP ese endpoint respondió `query poolType check error` para `poolType=all`; por eso no se usa como fallback silencioso.

El scanner usa:

- `tvl` como TVL USD.
- `day.volume` como volumen 24h USD reportado.
- `week.volume` como volumen 7d USD reportado.
- `feeRate` como tasa decimal (por ejemplo `0.0025` = 25 bps).
- `day.apr` como `reported_apr`.
- suma de `day.rewardApr` como `reward_apr`.

Si un campo no existe o no es numérico, queda como `None`; nunca se convierte en cero para fabricar una señal.

## Fuentes

- [Raydium API v3](https://api-v3.raydium.io/docs/)
- [Raydium API reference](https://docs.raydium.io/api-reference/api-v3-endpoints/pools/get-pools-by-token-mint)
- [Raydium integration guide](https://docs.raydium.io/integration-guides/aggregator)
