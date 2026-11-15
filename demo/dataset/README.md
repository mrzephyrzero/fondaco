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
| `employees` | 120 | `internal` table, **`restricted` salary column** |

PII sits in its own table (`customers`), as a normalized warehouse would keep
it. (It was originally also a workaround for the whole-table label
over-approximation of the regex scanner; labeling is now column-precise, see
DECISIONS.md.)

`employees` is the table that exercises the column-level part of the label
model: the table is `internal`, `salary` is `restricted`. A template that never
references salary — `SELECT department FROM employees` — is `internal`; one
that reads salary anywhere, even only in a `WHERE` filter, is `restricted`.
Everywhere else in this dataset the column annotations sit at or below their
table's label, which means they change no decision at all.

**Time span.** Orders and stock movements run 2023-01-01 → 2026-12-31, and
deliveries are derived from real orders (so a parcel never ships before the
order exists, and `delivered_date` is set only when `status = 'delivered'`).
The span is wide rather than dense so that a question about a full year stays
under the adapter's 10 000-row cap: the widest scenario filter returns roughly
6 400 rows. Narrowing the span without lowering the row counts would break that.

`init/03_readonly_role.sql` creates `fondaco_ro`, the SELECT-only role the
app connects as — write denial is enforced at the DB layer.
