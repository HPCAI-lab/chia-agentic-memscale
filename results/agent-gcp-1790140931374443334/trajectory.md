# Measured agent trajectory

Step 0 is a freshly measured scripted baseline.
Steps 1+ are Gemini selections. Historical manual runs are context only.

| Step | Selected by | Configuration | Status | Throughput (cases/s) |
| --- | --- | --- | --- | --- |
| 0 | scripted_baseline | 3P / 1E / B1 | success | 2.021636 |
| 1 | gemini | 3P / 1E / B2 | success | 2.012081 |
| 2 | gemini | 3P / 1E / B3 | success | 2.021549 |
| 3 | gemini | 3P / 2E / B1 | success | 2.005932 |
| 4 | gemini | 3P / 2E / B2 | success | 2.990864 |

This small water-SCF demonstration measures orchestration throughput.
Memory use, data-placement policies, 1H9T scaling, and global optimality are unmeasured.
Differences include launch overhead and run-to-run variation; repeated trials are needed
before making a robust performance claim.
