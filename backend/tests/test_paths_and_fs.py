from pathlib import Path

from shellgeist.runtime.paths import resolve_repo_path
from shellgeist.tools.fs import read_file, list_files


def test_resolve_repo_path_rejects_absolute_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = Path("/tmp/shellgeist-outside")

    # When given an absolute path outside the workspace, we should get a clear PermissionError.
    try:
        resolve_repo_path(root, str(outside))
    except PermissionError as e:
        msg = str(e)
        assert "Access denied" in msg
        assert "absolute path" in msg
        assert "project root" in msg
    else:
        raise AssertionError("Expected PermissionError for absolute path outside root")


def test_read_file_and_list_files_with_relative_paths(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "README.md").write_text("hello", encoding="utf-8")
    (root / "test").mkdir()
    (root / "test" / "ping.py").write_text("print('pong')", encoding="utf-8")

    # read_file with relative path
    content = read_file(path="README.md", root=str(root))
    assert content == "hello"

    # list_files at root
    entries = list_files(directory=".", root=str(root))
    assert "README.md" in entries

    # list_files in subdirectory
    test_entries = list_files(directory="test", root=str(root))
    assert "ping.py" in test_entries

