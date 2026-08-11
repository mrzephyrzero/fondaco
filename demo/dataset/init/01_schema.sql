-- Fondaco demo dataset: synthetic warehouse schema with label annotations.
-- Labels ride on COMMENTs as 'label:<level> <optional description>'.
-- Anything without a label annotation is treated as restricted (fail closed).

CREATE TABLE products (
    id serial PRIMARY KEY,
    sku text NOT NULL,
    name text NOT NULL,
    category text NOT NULL,
    unit_price numeric(10, 2) NOT NULL
);
COMMENT ON TABLE products IS 'label:public Product catalog';
COMMENT ON COLUMN products.id IS 'label:public';
COMMENT ON COLUMN products.sku IS 'label:public Stock keeping unit';
COMMENT ON COLUMN products.name IS 'label:public';
COMMENT ON COLUMN products.category IS 'label:public';
COMMENT ON COLUMN products.unit_price IS 'label:public List price';

CREATE TABLE customers (
    id serial PRIMARY KEY,
    name text NOT NULL,
    email text NOT NULL,
    phone text NOT NULL,
    city text NOT NULL
);
COMMENT ON TABLE customers IS 'label:restricted Customer master data (PII)';
COMMENT ON COLUMN customers.id IS 'label:internal';
COMMENT ON COLUMN customers.name IS 'label:restricted PII';
COMMENT ON COLUMN customers.email IS 'label:restricted PII';
-- customers.phone intentionally left without a label annotation:
-- it must fall back to restricted (adapter-contract.md s2.1).
COMMENT ON COLUMN customers.city IS 'label:confidential';

CREATE TABLE orders (
    id serial PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES customers (id),
    region text NOT NULL,
    status text NOT NULL,
    order_date date NOT NULL,
    total_amount numeric(10, 2) NOT NULL
);
COMMENT ON TABLE orders IS 'label:internal Customer orders';
COMMENT ON COLUMN orders.id IS 'label:internal';
COMMENT ON COLUMN orders.customer_id IS 'label:internal Reference to customers';
COMMENT ON COLUMN orders.region IS 'label:internal';
COMMENT ON COLUMN orders.status IS 'label:internal';
COMMENT ON COLUMN orders.order_date IS 'label:internal';
COMMENT ON COLUMN orders.total_amount IS 'label:internal';

CREATE TABLE deliveries (
    id serial PRIMARY KEY,
    order_id integer NOT NULL REFERENCES orders (id),
    carrier text NOT NULL,
    shipped_date date NOT NULL,
    -- Null until the parcel actually arrives: only status 'delivered' has one.
    delivered_date date,
    status text NOT NULL
);
COMMENT ON TABLE deliveries IS 'label:internal Outbound deliveries';
COMMENT ON COLUMN deliveries.id IS 'label:internal';
COMMENT ON COLUMN deliveries.order_id IS 'label:internal';
COMMENT ON COLUMN deliveries.carrier IS 'label:internal';
COMMENT ON COLUMN deliveries.shipped_date IS 'label:internal';
COMMENT ON COLUMN deliveries.delivered_date IS 'label:internal';
COMMENT ON COLUMN deliveries.status IS 'label:internal';

CREATE TABLE stock_movements (
    id serial PRIMARY KEY,
    product_id integer NOT NULL REFERENCES products (id),
    warehouse text NOT NULL,
    movement_type text NOT NULL,
    quantity integer NOT NULL,
    moved_at timestamp NOT NULL
);
COMMENT ON TABLE stock_movements IS 'label:internal Warehouse stock movements';
COMMENT ON COLUMN stock_movements.id IS 'label:internal';
COMMENT ON COLUMN stock_movements.product_id IS 'label:internal';
COMMENT ON COLUMN stock_movements.warehouse IS 'label:internal';
COMMENT ON COLUMN stock_movements.movement_type IS 'label:internal';
COMMENT ON COLUMN stock_movements.quantity IS 'label:internal';
COMMENT ON COLUMN stock_movements.moved_at IS 'label:internal';

-- employees exists to make the column-level part of the label model visible.
-- The table is labeled 'internal', but salary is 'restricted', and the policy
-- engine takes the max over the table label AND every one of its columns. So
-- *every* query against employees comes out restricted — including
-- `SELECT department FROM employees`, which never touches salary.
--
-- That is the documented over-approximation of label-model.md §4: the scanner
-- does not work out which columns a template reads, so it assumes all of them.
-- It over-restricts, which is the safe direction, and it means a table is only
-- as usable as its most sensitive column. Without a table shaped like this one,
-- nothing in the demo exercises that rule (every other table here has columns
-- at or below its own label, so their column annotations change no decision).
CREATE TABLE employees (
    id serial PRIMARY KEY,
    name text NOT NULL,
    department text NOT NULL,
    hire_date date NOT NULL,
    salary numeric(10, 2) NOT NULL
);
COMMENT ON TABLE employees IS 'label:internal Staff roster; salary raises the whole table';
COMMENT ON COLUMN employees.id IS 'label:internal';
COMMENT ON COLUMN employees.name IS 'label:internal';
COMMENT ON COLUMN employees.department IS 'label:internal';
COMMENT ON COLUMN employees.hire_date IS 'label:internal';
COMMENT ON COLUMN employees.salary IS 'label:restricted Payroll';

-- Indexes on the columns the demo scenarios filter and join on. Not needed at
-- 51k rows; present so the schema reads like one a DBA would recognize.
CREATE INDEX ON orders (order_date);
CREATE INDEX ON orders (customer_id);
CREATE INDEX ON deliveries (shipped_date);
CREATE INDEX ON deliveries (order_id);
CREATE INDEX ON stock_movements (moved_at);
CREATE INDEX ON stock_movements (product_id);
