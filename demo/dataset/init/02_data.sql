-- Deterministic synthetic data (~51k rows). setseed makes random() reproducible.

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

INSERT INTO orders (customer_id, region, status, order_date, total_amount)
SELECT
    1 + floor(random() * 1000)::int,
    (ARRAY['north', 'south', 'east', 'west'])[1 + floor(random() * 4)::int],
    (ARRAY['pending', 'paid', 'shipped', 'delivered', 'cancelled'])[1 + floor(random() * 5)::int],
    date '2024-01-01' + floor(random() * 912)::int,
    round((random() * 4990 + 10)::numeric, 2)
FROM generate_series(1, 20000) g;

INSERT INTO deliveries (order_id, carrier, shipped_date, delivered_date, status)
SELECT
    1 + floor(random() * 20000)::int,
    (ARRAY['DHL', 'UPS', 'GLS', 'BRT'])[1 + floor(random() * 4)::int],
    s.d,
    s.d + 1 + floor(random() * 6)::int,
    (ARRAY['in_transit', 'delivered', 'returned'])[1 + floor(random() * 3)::int]
FROM (
    SELECT date '2024-01-05' + floor(random() * 900)::int AS d
    FROM generate_series(1, 15000)
) s;

INSERT INTO stock_movements (product_id, warehouse, movement_type, quantity, moved_at)
SELECT
    1 + floor(random() * 200)::int,
    (ARRAY['VE-1', 'VE-2', 'MI-1'])[1 + floor(random() * 3)::int],
    (ARRAY['inbound', 'outbound', 'adjustment'])[1 + floor(random() * 3)::int],
    1 + floor(random() * 500)::int,
    timestamp '2024-01-01 00:00:00' + random() * 790 * interval '1 day'
FROM generate_series(1, 15000) g;

ANALYZE;
