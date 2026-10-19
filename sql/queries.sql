-- name: payers
-- 1. Список плательщиков, сортировка по имени.
SELECT id, full_name FROM payers ORDER BY full_name, id;

-- name: invoices
-- 2. Список снимков счетов, сортировка по номеру.
SELECT id, payer_id, account_number, amount_cents
FROM invoices ORDER BY account_number, id;

-- name: all_payments
-- 3. JOIN: все платежи с плательщиком и счётом.
SELECT p.id, u.full_name, i.account_number,
       i.amount_cents AS invoice_amount_cents,
       p.amount_cents, p.method, p.paid_at, p.status,
       p.commission_cents, p.total_charged_cents,
       p.remaining_cents, p.overpayment_cents
FROM payments AS p
JOIN invoices AS i ON i.id = p.invoice_id
JOIN payers AS u ON u.id = i.payer_id
ORDER BY p.paid_at DESC, p.id;

-- name: payment_detail
-- 4. JOIN и WHERE: карточка платежа по UUID (:payment_id).
SELECT p.id, u.full_name, i.account_number,
       i.amount_cents AS invoice_amount_cents,
       p.amount_cents, p.method, p.paid_at, p.status,
       p.commission_cents, p.total_charged_cents,
       p.remaining_cents, p.overpayment_cents
FROM payments AS p
JOIN invoices AS i ON i.id = p.invoice_id
JOIN payers AS u ON u.id = i.payer_id
WHERE p.id = :payment_id;

-- name: partial_payments
-- 5. WHERE: операции с неполной оплатой.
SELECT p.id, u.full_name, i.account_number,
       i.amount_cents AS invoice_amount_cents,
       p.amount_cents, p.method, p.paid_at, p.status,
       p.commission_cents, p.total_charged_cents,
       p.remaining_cents, p.overpayment_cents
FROM payments AS p
JOIN invoices AS i ON i.id = p.invoice_id
JOIN payers AS u ON u.id = i.payer_id
WHERE p.status = 'частичная оплата'
ORDER BY p.paid_at DESC, p.id;

-- name: search_payments
-- 6. WHERE: поиск по статусу, способу, плательщику, дате, сумме.
-- Все параметры передаются отдельно. NULL отключает соответствующий фильтр.
SELECT p.id, u.full_name, i.account_number,
       i.amount_cents AS invoice_amount_cents,
       p.amount_cents, p.method, p.paid_at, p.status,
       p.commission_cents, p.total_charged_cents,
       p.remaining_cents, p.overpayment_cents
FROM payments AS p
JOIN invoices AS i ON i.id = p.invoice_id
JOIN payers AS u ON u.id = i.payer_id
WHERE (:status IS NULL OR p.status = :status)
  AND (:method IS NULL OR p.method = :method)
  AND (:payer_id IS NULL OR u.id = :payer_id)
  AND (:date_from IS NULL OR p.paid_at >= :date_from)
  AND (:date_to IS NULL OR p.paid_at < :date_to)
  AND (:min_amount IS NULL OR p.amount_cents >= :min_amount)
ORDER BY p.paid_at DESC, p.id
LIMIT :limit OFFSET :offset;

-- name: payment_count
-- 7. Агрегат: количество операций.
SELECT COUNT(*) AS payment_count FROM payments;

-- name: payment_totals
-- 8. Агрегаты: суммы, комиссии, минимум, максимум и средний платёж.
SELECT COALESCE(SUM(amount_cents), 0) AS total_amount_cents,
       COALESCE(SUM(commission_cents), 0) AS total_commission_cents,
       COALESCE(MIN(amount_cents), 0) AS min_amount_cents,
       COALESCE(MAX(amount_cents), 0) AS max_amount_cents,
       COALESCE(AVG(amount_cents), 0) AS avg_amount_cents
FROM payments;

-- name: status_counts
-- 9. GROUP BY: количество и сумма платежей по статусам.
SELECT status, COUNT(*) AS payment_count,
       SUM(amount_cents) AS amount_cents
FROM payments GROUP BY status ORDER BY status;

-- name: method_totals
-- 10. GROUP BY: количество, суммы и комиссии по способам оплаты.
SELECT method, COUNT(*) AS payment_count,
       SUM(amount_cents) AS amount_cents,
       SUM(commission_cents) AS commission_cents
FROM payments GROUP BY method ORDER BY method;

-- name: payer_statistics
-- 11. GROUP BY и HAVING: плательщики с числом операций больше порога.
SELECT u.id, u.full_name, COUNT(p.id) AS payment_count,
       COALESCE(SUM(p.amount_cents), 0) AS amount_cents
FROM payers AS u
LEFT JOIN invoices AS i ON i.payer_id = u.id
LEFT JOIN payments AS p ON p.invoice_id = i.id
GROUP BY u.id, u.full_name
HAVING COUNT(p.id) > :min_count
ORDER BY payment_count DESC, u.full_name, u.id;
