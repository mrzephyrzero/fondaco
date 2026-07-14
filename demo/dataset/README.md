# Demo dataset

Synthetic warehouse-flavored data (~51k rows), deterministic (`setseed`),
loaded automatically by `docker compose up` via `init/*.sql`:

| Table | Rows | Label |
|---|---|---|
| `products` | 200 | `public` |
| `customers` | 1 000 | `restricted` (PII; `phone` deliberately unlabeled → falls back to `restricted`) |
| `orders` | 20 000 | `internal` |
| `deliveries` | 15 000 | `internal` |
| `stock_movements` | 15 000 | `internal` |

PII sits in its own table (`customers`) so that internal-clearance demo
queries over `orders`/`deliveries`/`stock_movements` pass policy despite the
whole-table label over-approximation (see DECISIONS.md).

`init/03_readonly_role.sql` creates `fondaco_ro`, the SELECT-only role the
app connects as — write denial is enforced at the DB layer.
