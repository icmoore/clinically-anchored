"""A stand-in for the supabase-py client that talks to a real (local) Postgres, so the api can
be exercised end to end against the real migrations. It supports exactly the calls the api
makes and returns what PostgREST would: JSON-shaped rows (uuids and timestamps as strings),
an object for a function that returns one row, and database errors whose text carries the
function's exception message (the api maps errors by that text)."""

import datetime as dt
import re
import uuid
from decimal import Decimal

from psycopg.types.json import Jsonb

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def jsonify(value):
    if isinstance(value, dict):
        return {k: jsonify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonify(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _param(value):
    return Jsonb(value) if isinstance(value, dict) else value


def _ident(name: str) -> str:
    assert _IDENT.match(name), name
    return name


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, conn, table: str):
        self.conn, self.table = conn, _ident(table)
        self.cols = "*"
        self.where: list[tuple[str, list]] = []
        self.order_by: list[str] = []
        self.limit_n: int | None = None
        self.offset_n = 0
        self.op, self.payload = "select", None

    def select(self, cols="*", **_k):
        assert re.match(r"^[a-z0-9_,\s*]+$", cols), cols
        self.cols = cols
        return self

    def eq(self, col, val):
        col = _ident(col)
        if val is None or isinstance(val, bool):
            self.where.append((f"{col} = %s", [val]))
        else:
            self.where.append((f"{col}::text = %s", [str(val)]))
        return self

    def is_(self, col, val):
        assert val == "null"
        self.where.append((f"{_ident(col)} is null", []))
        return self

    def in_(self, col, vals):
        self.where.append((f"{_ident(col)}::text = any(%s)", [[str(v) for v in vals]]))
        return self

    def order(self, col, desc=False):
        self.order_by.append(f"{_ident(col)} {'desc' if desc else 'asc'}")
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def range(self, a, b):
        self.offset_n, self.limit_n = a, b - a + 1
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _where(self):
        if not self.where:
            return "", []
        return " where " + " and ".join(w for w, _ in self.where), [p for _, ps in self.where for p in ps]

    def execute(self):
        where, params = self._where()
        if self.op == "insert":
            cols = [_ident(c) for c in self.payload]
            sql = (f"insert into {self.table} ({', '.join(cols)}) "
                   f"values ({', '.join(['%s'] * len(cols))}) returning *")
            params = [_param(v) for v in self.payload.values()]
        elif self.op == "update":
            sets = [f"{_ident(c)} = %s" for c in self.payload]
            sql = f"update {self.table} set {', '.join(sets)}{where} returning *"
            params = [_param(v) for v in self.payload.values()] + params
        elif self.op == "delete":
            sql = f"delete from {self.table}{where} returning *"
        else:
            sql = f"select {self.cols} from {self.table}{where}"
            if self.order_by:
                sql += " order by " + ", ".join(self.order_by)
            if self.limit_n is not None:
                sql += f" limit {int(self.limit_n)} offset {int(self.offset_n)}"
        return Result([jsonify(r) for r in self.conn.execute(sql, params).fetchall()])


class Rpc:
    def __init__(self, conn, name, params):
        self.conn, self.name, self.params = conn, _ident(name), params

    def execute(self):
        args = ", ".join(f"{_ident(k)} => %s" for k in self.params)
        values = [_param(v) for v in self.params.values()]
        returns = self.conn.execute(
            "select prorettype::regtype::text as t from pg_proc where proname = %s", (self.name,)
        ).fetchone()["t"]
        if returns == "jsonb":
            sql = f"select {self.name}({args}) as r"
        else:
            sql = f"select to_jsonb(t) as r from {self.name}({args}) t"
        return Result(jsonify(self.conn.execute(sql, values).fetchone()["r"]))


class _Auth:
    def get_user(self, jwt):
        if not jwt.startswith("user:"):
            raise ValueError("bad jwt")
        user = type("U", (), {"id": jwt[5:], "email": "doc@example.com"})()
        return type("UR", (), {"user": user})()


class PgSupabase:
    def __init__(self, conn):
        self.conn = conn
        conn.execute("set timezone = 'UTC'")
        self.auth = _Auth()

    def table(self, name):
        return Query(self.conn, name)

    def rpc(self, name, params):
        return Rpc(self.conn, name, params)
