import datetime
import decimal
import json
import re
from typing import List, Optional, Union, Dict
from urllib.parse import urlparse
import io
import csv

from sqlalchemy import create_engine, text, MetaData, Table, Column, inspect
from sqlalchemy.exc import SQLAlchemyError
from rapidfuzz import fuzz, process

class MySqlHandler():
    def __init__(self, host:str, user: str, password: str, database: str, port: Optional[str] = None) -> None:
        """
        Initializes the MySqlFunctions object using SQLAlchemy and establishes a connection engine.
        """
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.port = port
        self.engine = None # SQLAlchemy engine replaces direct connection
        self.metadata = MetaData() # SQLAlchemy metadata for schema reflection

    def connect(self, connection_string: str = None) -> None:
        """
        Establishes a connection to the MySQL database using SQLAlchemy.
        """
        if not connection_string:
            port_part = f":{self.port}" if self.port else "" # Add port only if it's not None or empty
            connection_uri = f"mysql+mysqlconnector://{self.user}:{self.password}@{self.host}{port_part}/{self.database}"
            try:
                self.engine = create_engine(connection_uri)
                # Test connection
                with self.engine.connect() as connection:
                    connection.execute(text("SELECT 1")) # Simple test query
                print("Connected to the database successfully using SQLAlchemy.")
            except SQLAlchemyError as err:
                print(f"Error connecting to the database using SQLAlchemy: {err}")
                self.engine = None
        else:
            try:
                parsed = urlparse(connection_string)
                if parsed.scheme != "mysql":
                    raise ValueError("Invalid scheme. Use 'mysql://'.")

                connection_uri = f"mysql+mysqlconnector://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port or 3306}{parsed.path}"
                self.engine = create_engine(connection_uri)
                 # Test connection
                with self.engine.connect() as connection:
                    connection.execute(text("SELECT 1")) # Simple test query
            except SQLAlchemyError as e:
                raise ConnectionError(f"Failed to connect using SQLAlchemy: {e}") from e

    def disconnect(self) -> None:
        """
        Disposes of the SQLAlchemy engine (connection pool).
        """
        if self.engine:
            self.engine.dispose()
            print("Database connection pool disposed (SQLAlchemy).")
            self.engine = None

    def execute_sql_read(self, sql_query: str) -> str:
        """
        Executes a SQL read query against the database using SQLAlchemy and returns results in CSV format.
        """
        csv_output = io.StringIO()
        csv_writer = csv.writer(csv_output)

        try:
            if not self.engine:
                self.connect()
            if not self.engine:
                raise ConnectionError("No database engine available.")

            with self.engine.connect() as connection:
                result = connection.execute(text(sql_query))

                if not result.cursor.description: # Check if there are columns in the result
                    raise ValueError("Query returned no columns")

                fieldnames = [col[0] for col in result.cursor.description]
                csv_writer.writerow(fieldnames)

                for row in result:
                    # Convert non-string friendly types for CSV
                    row_values = []
                    for value in row:
                        if isinstance(value, (datetime.date, datetime.datetime)):
                            row_values.append(value.isoformat())
                        elif isinstance(value, (decimal.Decimal, bytes)):
                            row_values.append(str(value))
                        else:
                            row_values.append(str(value) if value is not None else '') # Ensure string conversion
                    csv_writer.writerow(row_values)

            return csv_output.getvalue()

        except (SQLAlchemyError, ValueError, ConnectionError) as e:
            csv_output = io.StringIO()
            csv_writer = csv.writer(csv_output)
            csv_writer.writerow(['error'])
            csv_writer.writerow([str(e)])
            return csv_output.getvalue()
        except Exception as e:
            csv_output = io.StringIO()
            csv_writer = csv.writer(csv_output)
            csv_writer.writerow(['error'])
            csv_writer.writerow([f"Execution error: {e}"])
            return csv_output.getvalue()


    def execute_sql_write(self, sql_query: str, commit: bool = True) -> dict:
        """
        Executes a SQL write query using SQLAlchemy and returns operation information.
        """
        try:
            if not self.engine:
                self.connect()
            if not self.engine:
                raise ConnectionError("No database engine available.")

            with self.engine.begin() as connection: # Use begin() for transactional context
                result = connection.execute(text(sql_query))
                affected_rows = result.rowcount # rowcount is still relevant

                return {
                    "row_count": affected_rows,
                    "success": True
                }

        except SQLAlchemyError as e:
            # Rollback is automatically handled by context manager with begin() on exception
            return {"error": f"Database error: {e}", "success": False}
        except Exception as e:
            # Rollback is automatically handled by context manager with begin() on exception
            return {"error": f"Execution error: {e}", "success": False}


    def fuzzy_find(self, user_value: str, column: str, table: str)-> List[str]:
        """
        Performs fuzzy matching on a column in a table using SQLAlchemy.
        """
        try:
            if not self.engine:
                self.connect()
            if not self.engine:
                raise ConnectionError("No database engine available.")

            with self.engine.connect() as connection:
                # WARNING: Ensure column and table are sanitized to prevent SQL injection
                query = text(f"SELECT DISTINCT `{column}` FROM `{table}` WHERE `{column}` IS NOT NULL")
                result = connection.execute(query)

                values = [row[0] for row in result.fetchall()]
                matches = process.extract(user_value, values, scorer=fuzz.WRatio, limit=5)
                return [match[0] for match in matches]

        except SQLAlchemyError as err:
            print(f"Database error: {err}")
            return []

    def schema_to_string(self) -> str:
        """Returns database schema structure without values using SQLAlchemy reflection."""
        if not self.engine:
            raise ConnectionError("Not connected to database")

        inspector = inspect(self.engine)
        database_name = self.database # Assuming database name is available

        schema = {"tables": []}
        table_names = inspector.get_table_names(schema=database_name) # Get table names for the database

        for table_name in table_names:
            columns_info = []
            for column in inspector.get_columns(table_name, schema=database_name):
                column_info = {
                    "name": column['name'],
                    "type": str(column['type']), # Convert type to string
                    "is_primary_key": column['primary_key'],
                    "is_foreign_key": False # Foreign key detection needs more logic, see below
                }
                columns_info.append(column_info)

            foreign_keys = []
            for fk in inspector.get_foreign_keys(table_name, schema=database_name):
                foreign_keys.append({"column": fk['constrained_columns'][0]}) # Assuming single column FK

            schema["tables"].append({
                "name": table_name,
                "columns": columns_info,
                "foreign_keys": foreign_keys
            })

        return json.dumps(schema, indent=2)
