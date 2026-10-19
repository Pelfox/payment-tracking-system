(() => {
    const filter = document.querySelector("[data-status-filter]");
    const panel = document.querySelector("[data-payment-filter]");
    const count = document.querySelector("[data-filter-count]");
    const emptyMessage = document.querySelector("[data-filter-empty]");
    const rows = Array.from(document.querySelectorAll("[data-payment-status]"));

    if (!filter || !panel || !count || !emptyMessage || rows.length === 0) {
        return;
    }

    const filterPayments = () => {
        let visibleCount = 0;

        rows.forEach((row) => {
            const matches = filter.value === "" || row.dataset.paymentStatus === filter.value;
            row.hidden = !matches;
            if (matches) {
                visibleCount += 1;
            }
        });

        count.textContent = `Показано платежей: ${visibleCount} из ${rows.length}`;
        emptyMessage.hidden = visibleCount !== 0;
    };

    filter.addEventListener("change", filterPayments);
    panel.hidden = false;
    filterPayments();
})();
