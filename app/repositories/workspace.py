"""Read-only catalog queries for the local operations workspace."""

from contextlib import closing

from app.db import database_step


class WorkspaceRepository:
    def __init__(self, connection):
        self.connection = connection

    def query(self, sql, params=()):
        with database_step("web-catalog"):
            with closing(self.connection.cursor()) as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()

    def customers(self):
        return tuple(dict(zip(("customer_id", "display_name"), row)) for row in self.query(
            "SELECT customer_id, display_name FROM customers ORDER BY customer_id"))

    def models(self):
        return tuple(dict(zip(("gpu_model_id", "manufacturer", "model_name", "memory_gb"), row))
                     for row in self.query("SELECT gpu_model_id, manufacturer, model_name, memory_gb "
                                           "FROM gpu_models ORDER BY gpu_model_id"))

    def servers(self):
        return tuple(dict(zip(("server_id", "server_name", "operational_status"), row))
                     for row in self.query("SELECT server_id, server_name, operational_status "
                                           "FROM servers ORDER BY server_id"))

    def database_name(self):
        return self.query("SELECT DATABASE()")[0][0]
