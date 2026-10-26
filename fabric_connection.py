import os
import struct
import pyodbc

from azure.identity import AzureCliCredential

def get_fabric_connection():
    server = os.environ["FABRIC_SQL_SERVER"]
    database = os.environ["FABRIC_SQL_DATABASE"]

    credential = AzureCliCredential()

    token = credential.get_token(
        "https://database.windows.net/.default"
    ).token

    token_bytes = token.encode("utf-16-le")

    token_struct = struct.pack(
        f"<I{len(token_bytes)}s",
        len(token_bytes),
        token_bytes
    )

    connection_string = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{server},1433;"
        f"Database={database};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
    )

    return pyodbc.connect(
        connection_string,
        attrs_before={1256: token_struct},
        timeout=15
    )

  
        
