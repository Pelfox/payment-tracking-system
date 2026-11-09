import argparse
from contextlib import closing
from pathlib import Path

from db.database import DATABASE_PATH, get_connection


def inspect_database(source: str | Path) -> list[dict]:
    tables = []
    with closing(get_connection(source)) as connection:
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall():
            name = row["name"]
            quoted = '"' + name.replace('"', '""') + '"'
            tables.append(
                {
                    "name": name,
                    "columns": [
                        dict(column)
                        for column in connection.execute(f"PRAGMA table_info({quoted})")
                    ],
                    "count": connection.execute(
                        f"SELECT COUNT(*) FROM {quoted}"
                    ).fetchone()[0],
                }
            )
    return tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DATABASE_PATH)
    args = parser.parse_args()
    for table in inspect_database(args.source):
        print(f"\nТаблица: {table['name']}; записей: {table['count']}")
        for column in table["columns"]:
            print(
                f"  {column['name']}: {column['type']}, "
                f"NOT NULL={column['notnull']}, PK={column['pk']}"
            )


if __name__ == "__main__":
    main()
