import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_PATH = BASE_DIR / "data" / "payments.sqlite"
SQL_DIR = BASE_DIR / "sql"


def get_connection(
    path: str | Path = DATABASE_PATH, *, readonly: bool = True
) -> sqlite3.Connection:
    path = Path(path).resolve()
    if readonly:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(path: str | Path = DATABASE_PATH) -> None:
    with closing(get_connection(path, readonly=False)) as connection:
        connection.executescript((SQL_DIR / "schema.sql").read_text("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Создать таблицы платежей SQLite")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args()
    initialize_database(args.database)
    print(f"Схема создана: {args.database}")


if __name__ == "__main__":
    main()
