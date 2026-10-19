PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS payers (
    id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL UNIQUE CHECK (length(trim(full_name)) > 0)
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY,
    payer_id INTEGER NOT NULL REFERENCES payers(id),
    account_number TEXT NOT NULL CHECK (length(trim(account_number)) > 0),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    UNIQUE (payer_id, account_number, amount_cents)
);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY NOT NULL,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    method TEXT NOT NULL CHECK (method IN ('карта', 'наличные')),
    paid_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('оплачен полностью', 'частичная оплата', 'переплата')
    ),
    commission_cents INTEGER NOT NULL CHECK (commission_cents >= 0),
    total_charged_cents INTEGER NOT NULL CHECK (
        total_charged_cents = amount_cents + commission_cents
    ),
    remaining_cents INTEGER NOT NULL CHECK (remaining_cents >= 0),
    overpayment_cents INTEGER NOT NULL CHECK (overpayment_cents >= 0)
);

CREATE INDEX IF NOT EXISTS payments_invoice_idx ON payments(invoice_id);
CREATE INDEX IF NOT EXISTS payments_date_idx ON payments(paid_at, id);
CREATE INDEX IF NOT EXISTS payments_status_idx ON payments(status);
