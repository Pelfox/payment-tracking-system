"""Исключения, которые ожидаемо могут возникнуть в приложении."""


class PaymentSystemError(Exception):
    """Базовое исключение системы учёта платежей."""


class PaymentStorageError(PaymentSystemError):
    """Базовое исключение хранилища платежей."""


class PaymentDataLoadError(PaymentStorageError):
    """Ошибка загрузки платежей из внешнего хранилища."""


class PaymentDataSaveError(PaymentStorageError):
    """Ошибка сохранения платежей во внешнее хранилище."""
