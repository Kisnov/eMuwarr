"""Task run persistence helpers."""
from __future__ import annotations

import sqlite3
from typing import Optional


def start_task(conn: sqlite3.Connection, name: str, summary: Optional[str] = None) -> int:
    cur = conn.execute(
        "INSERT INTO task_runs(name, status, summary) VALUES (?, 'running', ?)",
        (name, summary),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_task(conn: sqlite3.Connection, task_id: int, status: str, summary: Optional[str] = None, details: Optional[str] = None) -> None:
    conn.execute(
        """
        UPDATE task_runs
        SET status = ?, summary = COALESCE(?, summary), details = ?, finished_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, summary, details, task_id),
    )
    conn.commit()


def cerrar_tareas_interrumpidas(conn: sqlite3.Connection) -> int:
    """Al arrancar no corre ninguna tarea: las que siguen «running» murieron con la aplicación y se quedaban así para
    siempre (el mantenimiento conserva la última de cada nombre)."""
    n = conn.execute(
        "UPDATE task_runs SET status = 'failed', finished_at = CURRENT_TIMESTAMP, "
        "summary = 'Interrumpida al reiniciarse la aplicación' WHERE status = 'running'").rowcount
    conn.commit()
    return n


def discard_task(conn: sqlite3.Connection, task_id: int) -> None:
    """Quita una ejecución que no llegó a hacer nada (una tarea que espera turno y se reintenta al minuto siguiente)."""
    conn.execute("DELETE FROM task_runs WHERE id = ?", (task_id,))
    conn.commit()


def list_tasks(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM task_runs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def list_tasks_by_name(conn: sqlite3.Connection, name: str, limit: int = 20) -> list[dict]:
    """Phase 11D — run history/logs for one scheduled task (task_key == name)."""
    rows = conn.execute(
        "SELECT * FROM task_runs WHERE name = ? ORDER BY id DESC LIMIT ?",
        (name, limit),
    ).fetchall()
    return [dict(row) for row in rows]
