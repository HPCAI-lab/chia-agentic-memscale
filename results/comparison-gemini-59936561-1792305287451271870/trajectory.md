# Measured agent trajectory

Step 0 is a freshly measured scripted baseline.
Steps 1+ are gemini selections. Historical manual runs are context only.

| Step | Selected by | Configuration | Status | Throughput (cases/s) |
| --- | --- | --- | --- | --- |
| 0 | scripted_baseline | 3P / 1E / B1 | success | 0.491327 |
| 1 | gemini | 3P / 2E / B1 | success | 0.522710 |
| 2 | gemini | 3P / 2E / B2 | success | 0.751479 |
| 3 | gemini | 3P / 2E / B3 | success | 0.645650 |
| 4 | gemini | 3P / 3E / B2 | success | 0.746919 |

This small water-SCF demonstration measures orchestration throughput.
Memory use, data-placement policies, 1H9T scaling, and global optimality are unmeasured.
Differences include launch overhead and run-to-run variation; repeated trials are needed
before making a robust performance claim.
