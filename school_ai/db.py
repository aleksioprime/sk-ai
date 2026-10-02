"""Подключение к PostgreSQL и создание схемы."""

from importlib import resources

import psycopg
from pgvector.psycopg import register_vector

from .config import settings


def connect() -> psycopg.Connection:
    conn = psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password or None,
    )
    register_vector(conn)
    return conn


def init_schema() -> None:
    """Создаёт/обновляет таблицы. Безопасно запускать повторно."""

    sql = resources.files("school_ai").joinpath("sql/schema.sql").read_text()
    sql = sql.replace("{EMBED_DIMENSIONS}", str(settings.embed_dimensions))

    # Расширение vector создаёт администратор БД (см. deploy/install.sh),
    # здесь только проверяем, что оно есть.
    with psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password or None,
        autocommit=True,
    ) as conn:
        conn.execute(sql)
