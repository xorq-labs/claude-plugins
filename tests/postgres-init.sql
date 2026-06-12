-- Seed the Postgres service (compose.yaml) from tests/data/*.csv.
-- Runs once on first container init (docker-entrypoint-initdb.d).
-- Mirrors the CSV schemas in tests/data so the init-skill Postgres
-- examples (and the postgres-marked contract tests) have real tables to read.

CREATE TABLE customers (
    customer_id integer,
    name        text,
    age         integer,
    state       text,
    signup_date date,
    tier        text
);
COPY customers FROM '/seed/customers.csv' WITH (FORMAT csv, HEADER true);

CREATE TABLE products (
    product_id text,
    name       text,
    category   text,
    price      numeric,
    stock      integer,
    rating     numeric
);
COPY products FROM '/seed/products.csv' WITH (FORMAT csv, HEADER true);

CREATE TABLE transactions (
    txn_id      text,
    customer_id integer,
    product_id  text,
    amount      numeric,
    currency    text,
    category    text,
    "timestamp" timestamptz
);
COPY transactions FROM '/seed/transactions.csv' WITH (FORMAT csv, HEADER true);
