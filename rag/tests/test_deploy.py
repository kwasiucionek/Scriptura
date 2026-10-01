"""Local deployment regressions; no remote services or production database."""

import importlib.util
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "sqlite_snapshot", ROOT / "deploy" / "sqlite_snapshot.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_snapshot_includes_committed_wal(tmp_path):
    source, destination = tmp_path / "live.db", tmp_path / "snapshot.db"
    with closing(sqlite3.connect(source)) as origin:
        origin.execute("CREATE TABLE items (id INTEGER)")
        origin.commit()
        origin.execute("PRAGMA journal_mode=WAL")
        origin.execute("INSERT INTO items VALUES (1)")
        origin.commit()
        assert Path(str(source) + "-wal").stat().st_size > 0
        MODULE.snapshot(source, destination)
        with closing(sqlite3.connect(destination)) as target:
            assert target.execute("SELECT id FROM items").fetchall() == [(1,)]
            assert target.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert destination.stat().st_mode & 0o777 == 0o600


def test_snapshot_refuses_overwrite(tmp_path):
    source, destination = tmp_path / "live.db", tmp_path / "snapshot.db"
    with closing(sqlite3.connect(source)) as origin:
        origin.execute("CREATE TABLE items (id INTEGER)")
    destination.write_bytes(b"previous backup")
    with pytest.raises(FileExistsError):
        MODULE.snapshot(source, destination)
    assert destination.read_bytes() == b"previous backup"
    with pytest.raises(ValueError):
        MODULE.snapshot(source, source)


def test_snapshot_missing_source_does_not_create_database(tmp_path):
    source, destination = tmp_path / "missing.db", tmp_path / "snapshot.db"
    with pytest.raises(FileNotFoundError):
        MODULE.snapshot(source, destination)
    assert not source.exists()
    assert not destination.exists()


def test_snapshot_invalid_database_cleans_partial_backup(tmp_path):
    source, destination = tmp_path / "invalid.db", tmp_path / "snapshot.db"
    source.write_text("not a database")
    with pytest.raises(sqlite3.DatabaseError):
        MODULE.snapshot(source, destination)
    assert not destination.exists()


@pytest.mark.parametrize("arguments", [["--data"], ["--replace-db"], ["--oops"]])
def test_deploy_requires_explicit_database_replacement(arguments):
    result = subprocess.run(
        ["bash", str(ROOT / "deploy" / "deploy.sh"), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert "requires --replace-db" in result.stderr or "Usage:" in result.stderr


def test_setup_does_not_delete_wal():
    setup = (ROOT / "deploy" / "setup_after_rsync.sh").read_text()
    assert "rm -f /cytrus/scriptura/db.sqlite3-wal" not in setup
    assert "systemctl stop" in setup


def _replacement_script(path):
    deploy = (ROOT / "deploy" / "deploy.sh").read_text()
    block = deploy.split("<<'REPLACE'", 1)[1].split("\nREPLACE", 1)[0]
    return (
        re.search(r"<<'PY'\n(.*?)\nPY", block, re.S)
        .group(1)
        .replace('Path("/cytrus/scriptura/db.sqlite3")', f"Path({str(path)!r})")
    )


def _database_with_abandoned_wal(path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sqlite3, os, sys; db=sqlite3.connect(sys.argv[1]); "
            "db.execute('PRAGMA journal_mode=WAL'); "
            "db.execute('CREATE TABLE items (value TEXT)'); "
            "db.execute(\"INSERT INTO items VALUES ('OLD_WAL')\"); "
            "db.commit(); os._exit(0)",
            str(path),
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert Path(str(path) + "-wal").exists()


def _incoming_database(path):
    with closing(sqlite3.connect(str(path) + ".incoming")) as db:
        db.execute("CREATE TABLE items (value TEXT)")
        db.execute("INSERT INTO items VALUES ('INCOMING')")
        db.commit()


def test_replacement_checkpoints_old_wal_before_installing_incoming(tmp_path):
    path = tmp_path / "production.db"
    _database_with_abandoned_wal(path)
    _incoming_database(path)
    exec(_replacement_script(path), {})
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT value FROM items").fetchall() == [("INCOMING",)]


def test_failed_replacement_preserves_committed_production_data(tmp_path, monkeypatch):
    path = tmp_path / "production.db"
    _database_with_abandoned_wal(path)
    _incoming_database(path)

    def fail_replace(*args):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failure"):
        exec(_replacement_script(path), {})
    assert Path(str(path) + ".incoming").exists()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT value FROM items").fetchall() == [("OLD_WAL",)]


def test_nginx_overwrites_untrusted_forwarded_address():
    config = (ROOT / "deploy" / "nginx-scriptura.conf").read_text()
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in config
    assert "$proxy_add_x_forwarded_for" not in config


@pytest.mark.parametrize("behind_proxy", [False, True])
def test_forwarded_address_requires_proxy_configuration(settings, rf, behind_proxy):
    from rag.views import _client_ip

    settings.BEHIND_PROXY = behind_proxy
    request = rf.post(
        "/ask/stream", REMOTE_ADDR="192.0.2.1", HTTP_X_FORWARDED_FOR="198.51.100.1"
    )
    assert _client_ip(request) == ("198.51.100.1" if behind_proxy else "192.0.2.1")
