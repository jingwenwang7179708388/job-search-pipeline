# db.py - shared database connection; connection info comes from .env
import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")  # .env lives in the project root


def get_conn():
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", 3306)),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
        charset="utf8mb4",
        autocommit=False,
    )
