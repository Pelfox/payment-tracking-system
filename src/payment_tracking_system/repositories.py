from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from payment_tracking_system.exceptions import (
    PaymentDataLoadError,
    PaymentDataSaveError,
)
from payment_tracking_system.models import PaymentResult

_PAYMENT_RESULTS_ADAPTER = TypeAdapter(list[PaymentResult])


class PaymentRepository(ABC):
    """Интерфейс хранилища обработанных платежей."""

    @abstractmethod
    def save(self, result: PaymentResult) -> None:
        """Сохранить новый результат или обновить существующий."""

    @abstractmethod
    def get_by_id(self, payment_id: UUID) -> PaymentResult | None:
        """Получить результат по идентификатору платежа."""

    @abstractmethod
    def get_all(self) -> Sequence[PaymentResult]:
        """Получить все сохранённые результаты."""

    @abstractmethod
    def count(self) -> int:
        """Получить количество сохранённых результатов."""


class InMemoryPaymentRepository(PaymentRepository):
    """Хранилище платежей в памяти на основе словаря."""

    def __init__(self) -> None:
        self._results: dict[UUID, PaymentResult] = {}

    def save(self, result: PaymentResult) -> None:
        self._results[result.payment.id] = result

    def get_by_id(self, payment_id: UUID) -> PaymentResult | None:
        return self._results.get(payment_id)

    def get_all(self) -> Sequence[PaymentResult]:
        # Кортеж не позволяет вызывающему коду изменить внутреннюю коллекцию.
        return tuple(self._results.values())

    def count(self) -> int:
        return len(self._results)


class JsonPaymentRepository(InMemoryPaymentRepository):
    """Хранилище, сохраняющее коллекцию платежей в JSON-файле."""

    def __init__(self, file_path: str | Path) -> None:
        super().__init__()
        self._file_path = Path(file_path)
        self._load()

    def save(self, result: PaymentResult) -> None:
        # Сначала записываем новую коллекцию на диск и только затем меняем память.
        updated_results = dict(self._results)
        updated_results[result.payment.id] = result
        self._write(tuple(updated_results.values()))
        self._results = updated_results

    def _load(self) -> None:
        if not self._file_path.exists():
            return

        try:
            raw_data = self._file_path.read_bytes()
            results = _PAYMENT_RESULTS_ADAPTER.validate_json(raw_data)
        except (OSError, ValidationError) as error:
            raise PaymentDataLoadError(
                f"Не удалось загрузить платежи из {self._file_path}: {error}"
            ) from error

        for result in results:
            self._results[result.payment.id] = result

    def _write(self, results: Sequence[PaymentResult]) -> None:
        temporary_path = self._file_path.with_suffix(f"{self._file_path.suffix}.tmp")

        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            json_data = _PAYMENT_RESULTS_ADAPTER.dump_json(list(results), indent=2)
            temporary_path.write_bytes(json_data)
            temporary_path.replace(self._file_path)
        except (OSError, ValueError) as error:
            raise PaymentDataSaveError(
                f"Не удалось сохранить платежи в {self._file_path}: {error}"
            ) from error
