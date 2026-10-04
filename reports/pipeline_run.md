# Pipeline run

Written by `python -m xsell all`. Stage times are wall-clock seconds on the machine below; a stage that found its outputs current (for example fetch with a valid cache) is fast.

Data: 13,647,309 customer-month rows, 956,645 customers (all customers).

## Stages

| stage | seconds |
|---|---|
| fetch | 171.1 |
| warehouse | 79.3 |
| quality | 7.1 |
| analyze | 14.5 |
| performance | 1044.0 |
| train | 267.1 |
| total | 1583.1 |

Peak memory of the Python process (DuckDB runs inside it): 4,249 MB.

## Machine

|  | value |
|---|---|
| OS | Windows-11-10.0.26200-SP0 |
| CPU | AMD Ryzen 5 5500U with Radeon Graphics |
| Logical cores | 12 |
| RAM | 15.3 GB |
| Python | 3.12.12 |
| DuckDB | 1.5.6 |
| DuckDB memory_limit / threads | 4GB / 10 |
