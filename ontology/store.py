import json
import sqlite3
from contextlib import contextmanager

from ontology.semantic import validate


class Store:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                create table if not exists versions (
                    id integer primary key, parent integer, snapshot text not null);
                insert or ignore into versions values (0, null, '[]');
                create table if not exists batches (
                    id integer primary key, parent integer not null,
                    published integer references versions(id));
                create table if not exists changes (
                    id integer primary key, batch integer references batches(id),
                    object text not null, validation text not null,
                    decision text check(decision in ('approve','reject')));
                create trigger if not exists versions_no_update before update on versions
                    begin select raise(abort,'immutable version'); end;
                create trigger if not exists versions_no_delete before delete on versions
                    begin select raise(abort,'immutable version'); end;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                db.execute("pragma foreign_keys=on")
                yield db
        finally:
            db.close()

    def snapshot(self, version=None):
        return self.versioned_snapshot(version)[1]

    def versioned_snapshot(self, version=None):
        with self.connect() as db:
            if version is None:
                row = db.execute("select id,snapshot from versions order by id desc limit 1").fetchone()
            else:
                row = db.execute("select id,snapshot from versions where id=?", [version]).fetchone()
            if row is None:
                raise ValueError("Unknown version")
            return row[0], json.loads(row[1])

    def stage(self, path, objects):
        def identity(obj):
            return obj["kind"], obj["name"]

        with self.connect() as db:
            db.execute("begin immediate")
            parent, snapshot = db.execute("select id,snapshot from versions order by id desc limit 1").fetchone()
            previous = {identity(obj): obj for obj in json.loads(snapshot)}
            identities = [identity(obj) for obj in objects]
            if len(identities) != len(set(identities)):
                raise ValueError("Duplicate changes")
            batch = db.execute("insert into batches (parent) values (?)", [parent]).lastrowid
            for obj in objects:
                old = previous.get(identity(obj))
                if old != obj:
                    payload = dict(before=old, after=obj)
                    db.execute("insert into changes (batch,object,validation) values (?,?,?)",
                               [batch, json.dumps(payload, default=str), json.dumps(validate(path,obj), default=str)])
            return batch

    def review(self, batch):
        with self.connect() as db:
            db.execute("begin")
            row = db.execute("select parent,published from batches where id=?", [batch]).fetchone()
            if row is None:
                raise ValueError("Unknown batch")
            return dict(id=batch, parent=row[0], published=row[1], changes=self._changes(db, batch))

    def changes(self, batch):
        with self.connect() as db:
            return self._changes(db, batch)

    @staticmethod
    def _changes(db, batch):
        return [dict(id=i, **json.loads(obj), validation=json.loads(result), decision=decision)
                for i, obj, result, decision in db.execute(
                    "select id,object,validation,decision from changes where batch=? order by id", [batch])]

    def decide(self, change, decision):
        if decision not in {"approve", "reject"}:
            raise ValueError("Invalid decision")
        with self.connect() as db:
            db.execute("begin immediate")
            row = db.execute("""select changes.validation, batches.published from changes
                              join batches on batch=batches.id where changes.id=?""", [change]).fetchone()
            if row is None or row[1] is not None:
                raise ValueError("Change is unavailable")
            if decision == "approve" and not json.loads(row[0])["ok"]:
                raise ValueError("Validation failed")
            db.execute("update changes set decision=? where id=?", [decision, change])

    def publish(self, batch):
        with self.connect() as db:
            db.execute("begin immediate")
            row = db.execute("select parent,published from batches where id=?", [batch]).fetchone()
            if row is None:
                raise ValueError("Unknown batch")
            parent, published = row
            if published is not None:
                return published
            latest, snapshot = db.execute("select id,snapshot from versions order by id desc limit 1").fetchone()
            if parent != latest:
                raise ValueError("Stale batch. Stage against the current version.")
            changes = db.execute("select object,decision from changes where batch=? order by id", [batch]).fetchall()
            if any(decision is None for _, decision in changes):
                raise ValueError("Every change needs a decision")
            accepted = [json.loads(obj)["after"] for obj, decision in changes if decision == "approve"]
            if not accepted:
                raise ValueError("No approved changes")
            objects = {(obj["kind"],obj["name"]):obj for obj in json.loads(snapshot)}
            for obj in accepted:
                objects[obj["kind"],obj["name"]] = obj
            version = db.execute("insert into versions (parent,snapshot) values (?,?)",
                                 [parent,json.dumps(list(objects.values()))]).lastrowid
            db.execute("update batches set published=? where id=?", [version,batch])
            return version
