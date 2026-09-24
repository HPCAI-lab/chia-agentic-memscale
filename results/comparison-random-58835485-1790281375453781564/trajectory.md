# Measured agent trajectory

Step 0 is a freshly measured scripted baseline.
Steps 1+ are random selections. Historical manual runs are context only.

| Step | Selected by | Configuration | Status | Throughput (cases/s) |
| --- | --- | --- | --- | --- |
| 0 | scripted_baseline | 3P / 1E / B1 | success | 0.480227 |
| 1 | random | 3P / 3E / B3 | success | 0.830959 |
| 2 | random | 3P / 3E / B1 | success | 0.345977 |
| 3 | random | 3P / 3E / B2 | success | 0.610512 |
| 4 | random | 3P / 1E / B3 | success | 0.444661 |

This small water-SCF demonstration measures orchestration throughput.
Memory use, data-placement policies, 1H9T scaling, and global optimality are unmeasured.
Differences include launch overhead and run-to-run variation; repeated trials are needed
before making a robust performance claim.
