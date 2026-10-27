"""Command-line administration: ``flask --app app <command>``."""
from __future__ import annotations

import click
from werkzeug.security import generate_password_hash

from triage import simulator
from triage.store import Store

from .auth import validate_new_user


def register(app):
    @app.cli.command("init-db")
    def init_db():
        """Create or upgrade the database schema (safe to repeat)."""
        applied = app.extensions["store"].migrate()
        click.echo("Applied: " + ", ".join(applied) if applied else "Database is up to date.")

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--role", type=click.Choice(["clinician", "admin"]), default="clinician")
    @click.password_option()
    def create_user(username, role, password):
        """Create a user account."""
        err = validate_new_user(username, password, role)
        if err:
            raise click.ClickException(err)
        store = app.extensions["store"]
        conn = store.connect()
        try:
            with Store.write(conn):
                if Store.user_by_name(conn, username) is not None:
                    raise click.ClickException("That username is already taken.")
                uid = Store.create_user(conn, username, generate_password_hash(password), role)
                Store.insert_event(conn, type="user_created", actor="cli", entity_type="user",
                                   entity_id=str(uid), data={"detail": f"{username} ({role}) via CLI"})
        finally:
            conn.close()
        click.echo(f"Created {role} '{username}'.")

    @app.cli.command("load-demo")
    def load_demo():
        """Start a new run containing the synthetic demo scenario."""
        store = app.extensions["store"]
        conn = store.connect()
        try:
            run_id = simulator.load_demo(store, conn, "cli", app.config["TIMEZONE"])
        finally:
            conn.close()
        click.echo(f"Demo scenario loaded as run {run_id} (synthetic data).")
