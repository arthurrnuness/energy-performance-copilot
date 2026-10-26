import os
import psycopg
from dotenv import load_dotenv

load_dotenv()

def get_postgres_connection():
    return psycopg.connect(
        os.environ["COPILOT_DATABASE_URL"],
        sslmode="require"
    )

