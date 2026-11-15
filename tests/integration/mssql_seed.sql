-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 the Fondaco contributors
-- Seed for the live SQL Server adapter tests (test_sqlserver_live.py). Run
-- as an admin login against the test database; batches are separated by
-- lines holding only GO.
IF SCHEMA_ID('sales') IS NULL EXEC('CREATE SCHEMA sales')
GO
DROP TABLE IF EXISTS sales.orders;
DROP TABLE IF EXISTS sales.products;
DROP TABLE IF EXISTS sales.customers;
DROP TABLE IF EXISTS sales.numbers;
GO
CREATE TABLE sales.products (id int PRIMARY KEY, name nvarchar(100), category nvarchar(50));
CREATE TABLE sales.orders (id int PRIMARY KEY, region nvarchar(20), status nvarchar(20),
                           order_date date, amount decimal(10, 2));
CREATE TABLE sales.customers (id int PRIMARY KEY, city nvarchar(50), phone nvarchar(30));
CREATE TABLE sales.numbers (n int PRIMARY KEY);
GO
INSERT INTO sales.products VALUES (1, N'Pallet', N'storage'), (2, N'Crate', N'storage'),
                                  (3, N'Scanner', N'devices');
INSERT INTO sales.orders VALUES
  (1, N'north', N'shipped', '2025-05-02', 120.00),
  (2, N'north', N'open',    '2025-06-10',  80.50),
  (3, N'south', N'shipped', '2025-07-01', 310.00),
  (4, N'east',  N'open',    '2025-08-15',  42.00);
INSERT INTO sales.customers VALUES (1, N'Torino', N'+39 011 000000'), (2, N'Milano', N'+39 02 000000');
WITH d AS (SELECT n FROM (VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) v(n))
INSERT INTO sales.numbers SELECT a.n + 10 * b.n + 100 * c.n + 1000 * e.n FROM d a, d b, d c, d e;
GO
EXEC sp_addextendedproperty 'MS_Description', 'label:public Product catalogue', 'SCHEMA', 'sales', 'TABLE', 'products';
EXEC sp_addextendedproperty 'MS_Description', 'label:public', 'SCHEMA', 'sales', 'TABLE', 'products', 'COLUMN', 'id';
EXEC sp_addextendedproperty 'MS_Description', 'label:public', 'SCHEMA', 'sales', 'TABLE', 'products', 'COLUMN', 'name';
EXEC sp_addextendedproperty 'MS_Description', 'label:public', 'SCHEMA', 'sales', 'TABLE', 'products', 'COLUMN', 'category';
EXEC sp_addextendedproperty 'MS_Description', 'label:internal Orders', 'SCHEMA', 'sales', 'TABLE', 'orders';
EXEC sp_addextendedproperty 'MS_Description', 'label:internal', 'SCHEMA', 'sales', 'TABLE', 'orders', 'COLUMN', 'id';
EXEC sp_addextendedproperty 'MS_Description', 'label:internal', 'SCHEMA', 'sales', 'TABLE', 'orders', 'COLUMN', 'region';
EXEC sp_addextendedproperty 'MS_Description', 'label:internal', 'SCHEMA', 'sales', 'TABLE', 'orders', 'COLUMN', 'status';
EXEC sp_addextendedproperty 'MS_Description', 'label:internal', 'SCHEMA', 'sales', 'TABLE', 'orders', 'COLUMN', 'order_date';
EXEC sp_addextendedproperty 'MS_Description', 'label:confidential Revenue', 'SCHEMA', 'sales', 'TABLE', 'orders', 'COLUMN', 'amount';
EXEC sp_addextendedproperty 'MS_Description', 'label:confidential', 'SCHEMA', 'sales', 'TABLE', 'customers';
EXEC sp_addextendedproperty 'MS_Description', 'label:confidential', 'SCHEMA', 'sales', 'TABLE', 'customers', 'COLUMN', 'id';
EXEC sp_addextendedproperty 'MS_Description', 'label:confidential', 'SCHEMA', 'sales', 'TABLE', 'customers', 'COLUMN', 'city';
-- customers.phone deliberately unlabelled: must read as restricted.
EXEC sp_addextendedproperty 'MS_Description', 'label:public', 'SCHEMA', 'sales', 'TABLE', 'numbers';
EXEC sp_addextendedproperty 'MS_Description', 'label:public', 'SCHEMA', 'sales', 'TABLE', 'numbers', 'COLUMN', 'n';
GO
