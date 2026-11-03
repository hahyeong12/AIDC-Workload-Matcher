"""Check that the Windows temp workaround preserves existing files and options."""

from pathlib import Path
from types import SimpleNamespace

import conftest


def test_temp_config_respects_explicit_basetemp(monkeypatch, tmp_path):
    monkeypatch.setattr(conftest, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(conftest.sys, "platform", "win32")
    chosen = tmp_path / "user-chosen"
    config = SimpleNamespace(option=SimpleNamespace(basetemp=str(chosen)))
    conftest.pytest_configure(config)
    assert config.option.basetemp == str(chosen)
    assert not (tmp_path / ".pytest-tmp").exists()


def test_temp_config_uses_new_paths_without_removing_previous_files(monkeypatch, tmp_path):
    monkeypatch.setattr(conftest, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(conftest.sys, "platform", "win32")
    first = SimpleNamespace(option=SimpleNamespace(basetemp=None))
    conftest.pytest_configure(first)
    base = Path(first.option.basetemp)
    assert base.parent.is_dir()
    assert not base.exists()
    assert base.is_relative_to(tmp_path / ".pytest-tmp")
    sentinel = base.parent / "existing-user-file.txt"
    sentinel.write_text("preserve this file", encoding="utf-8")
    second = SimpleNamespace(option=SimpleNamespace(basetemp=None))
    conftest.pytest_configure(second)
    assert second.option.basetemp != first.option.basetemp
    assert sentinel.read_text(encoding="utf-8") == "preserve this file"
