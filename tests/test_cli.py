import subprocess
import sys

import pytest

from adlc import __version__
from adlc.cli import main


def test_version_constant_is_semver_like():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_version_flag_prints_version_and_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_unknown_flag_exits_nonzero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--definitely-not-a-flag"])
    assert excinfo.value.code != 0
    capsys.readouterr()


def test_module_entry_point_reports_version():
    result = subprocess.run(
        [sys.executable, "-m", "adlc", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert __version__ in result.stdout
