# Offline contract fault injection

Offline synthetic fault injection. Zero benchmark calls means zero additional calls after injection at the spied boundary; it does not assert that no Ray actor existed. SCF corruption is necessarily detected after output exists. These are correctness tests, not HPC performance measurements.

| Injected fault | Detection boundary | Benchmark calls after injection | Outcome |
| --- | --- | ---: | --- |
| exhausted attempt budget | runtime admission | 0 | rejected |
| corrupted SCF output with unchanged JSON | completion validation / offline audit | 0 | rejected after simulated output exists; no new workload launched by audit |
| duplicate configuration | runtime admission | 0 | rejected; no additional attempt |
| edited result record | offline consistency audit | 0 | excluded from policy analysis |
| out-of-space / wrong-type action | runtime admission | 0 | rejected; budget unchanged |
| reordered records / receipts | offline sequence audit | 0 | excluded from policy analysis |
| stale receipt | runtime admission | 0 | rejected |
