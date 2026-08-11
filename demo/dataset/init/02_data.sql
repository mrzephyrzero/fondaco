-- Deterministic synthetic data (~51k rows). setseed makes random() reproducible.
--
-- The history spans 2023-01-01 to 2026-12-31 so that a question about "2026"
-- is answered with a whole year of data, not a partial one. The span is wide
-- rather than dense on purpose: raw query results must stay under the adapter's
-- 10 000-row cap (see demo/scenarios.md), and the widest scenario filter
-- ("since 2025-10-01") returns roughly 6 400 of the 20 000 orders at this
-- spread. Shrinking the span without shrinking the row counts would push that
-- filter over the cap.

SELECT setseed(0.42);

INSERT INTO products (sku, name, category, unit_price)
SELECT
    'SKU-' || lpad(g::text, 5, '0'),
    'Product ' || g,
    (ARRAY['tools', 'fasteners', 'electrical', 'plumbing', 'safety'])[1 + floor(random() * 5)::int],
    round((random() * 490 + 10)::numeric, 2)
FROM generate_series(1, 200) g;

INSERT INTO customers (name, email, phone, city)
SELECT
    'Customer ' || g,
    'customer' || g || '@example.test',
    '+39 0' || (100000000 + floor(random() * 899999999))::bigint,
    (ARRAY['Venezia', 'Milano', 'Torino', 'Bologna', 'Napoli', 'Genova'])[1 + floor(random() * 6)::int]
FROM generate_series(1, 1000) g;

INSERT INTO employees (name, department, hire_date, salary)
SELECT
    'Employee ' || g,
    (ARRAY['warehouse', 'logistics', 'sales', 'finance'])[1 + floor(random() * 4)::int],
    date '2019-01-01' + floor(random() * 2500)::int,
    round((random() * 45000 + 25000)::numeric, 2)
FROM generate_series(1, 120) g;

-- 1461 days = 2023-01-01 .. 2026-12-31 inclusive.
INSERT INTO orders (customer_id, region, status, order_date, total_amount)
SELECT
    1 + floor(random() * 1000)::int,
    (ARRAY['north', 'south', 'east', 'west'])[1 + floor(random() * 4)::int],
    (ARRAY['pending', 'paid', 'shipped', 'delivered', 'cancelled'])[1 + floor(random() * 5)::int],
    date '2023-01-01' + floor(random() * 1461)::int,
    round((random() * 4990 + 10)::numeric, 2)
FROM generate_series(1, 20000) g;

-- Deliveries are derived from real orders rather than generated independently,
-- so a delivery cannot ship before the order that caused it exists, and
-- delivered_date is set only when the parcel actually arrived.
INSERT INTO deliveries (order_id, carrier, shipped_date, delivered_date, status)
SELECT
    d.order_id,
    d.carrier,
    d.shipped_date,
    CASE WHEN d.status = 'delivered' THEN d.shipped_date + d.transit_days ELSE NULL END,
    d.status
FROM (
    SELECT
        o.id AS order_id,
        (ARRAY['DHL', 'UPS', 'GLS', 'BRT'])[1 + floor(random() * 4)::int] AS carrier,
        o.order_date + (1 + floor(random() * 5)::int) AS shipped_date,
        1 + floor(random() * 6)::int AS transit_days,
        (ARRAY['in_transit', 'delivered', 'returned'])[1 + floor(random() * 3)::int] AS status
    FROM orders o
    ORDER BY random()
    LIMIT 15000
) d;

INSERT INTO stock_movements (product_id, warehouse, movement_type, quantity, moved_at)
SELECT
    1 + floor(random() * 200)::int,
    (ARRAY['VE-1', 'VE-2', 'MI-1'])[1 + floor(random() * 3)::int],
    (ARRAY['inbound', 'outbound', 'adjustment'])[1 + floor(random() * 3)::int],
    1 + floor(random() * 500)::int,
    timestamp '2023-01-01 00:00:00' + random() * 1460 * interval '1 day'
FROM generate_series(1, 15000) g;

ANALYZE;
