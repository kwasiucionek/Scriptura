"""Deployment flag tests with fake executables; no SSH, pip or services."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def executable(path, body):
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o755)


@pytest.mark.parametrize("with_eval", [False, True])
def test_dependency_selection(tmp_path, with_eval):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    shutil.copyfile(
        ROOT / "deploy" / "install_dependencies.sh", deploy / "install_dependencies.sh"
    )
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    calls = tmp_path / "calls.jsonl"
    executable(
        venv / "python",
        "import json, os, sys\n"
        "with open(os.environ['CAPTURE_PATH'], 'a') as f:\n"
        "    f.write(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(), "
        "'telemetry': os.environ.get('MLFLOW_ENABLE_TELEMETRY')}) + '\\n')\n",
    )
    arguments = ["--eval"] if with_eval else []
    result = subprocess.run(
        ["bash", str(deploy / "install_dependencies.sh"), *arguments],
        env={**os.environ, "CAPTURE_PATH": str(calls)},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in calls.read_text().splitlines()]
    assert rows[0]["args"] == [
        "-m",
        "pip",
        "install",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "-e",
        ".[prod,harvest,eval]" if with_eval else ".[prod,harvest]",
    ]
    assert rows[0]["cwd"] == str(tmp_path)
    assert rows[1]["args"] == ["-m", "pip", "check"]
    assert len(rows) == (3 if with_eval else 2)
    if with_eval:
        assert rows[2]["args"][0] == "-c"
        assert "require_mlflow" in rows[2]["args"][1]
        assert rows[2]["telemetry"] == "false"


def test_dependency_installer_rejects_unknown_flags_before_pip(tmp_path):
    result = subprocess.run(
        ["bash", str(ROOT / "deploy" / "install_dependencies.sh"), "--unknown"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    assert "Usage:" in result.stderr


@pytest.mark.parametrize("with_eval", [False, True])
def test_code_deploy_forwards_eval_flag_without_data_replacement(tmp_path, with_eval):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "ssh.jsonl"
    executable(
        bin_dir / "git",
        "import sys\nif 'rev-parse' in sys.argv: print('master')\n",
    )
    executable(
        bin_dir / "ssh",
        "import json, os, sys\n"
        "with open(os.environ['CAPTURE_PATH'], 'a') as f:\n"
        "    f.write(json.dumps({'args': sys.argv[1:], 'stdin': sys.stdin.read()}) + '\\n')\n",
    )
    arguments = ["--eval"] if with_eval else []
    result = subprocess.run(
        ["bash", str(ROOT / "deploy" / "deploy.sh"), *arguments],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CAPTURE_PATH": str(calls),
            "BRANCH": "master",
        },
        input="",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in calls.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["args"][-4:] == [
        "-s",
        "--",
        "master",
        "true" if with_eval else "false",
    ]
    script = rows[0]["stdin"]
    assert "install_dependencies.sh --eval" in script
    assert "--ff-only" in script and "reset" not in script
    assert "db.sqlite3.incoming" not in script
    assert "restart scriptura-web" in rows[1]["args"][-1]


def test_data_setup_passes_only_validated_install_arguments():
    script = (ROOT / "deploy" / "setup_after_rsync.sh").read_text()
    assert "INSTALL_ARGS=(--eval)" in script
    assert 'deploy/install_dependencies.sh "${INSTALL_ARGS[@]}"' in script
    assert script.index("Usage: $0 [--eval]") < script.index("systemctl stop")
