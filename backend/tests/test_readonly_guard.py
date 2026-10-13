"""Read-only by construction 검사: 도구 레이어 소스에 쓰기·exec·secret·셸 경로가 없어야 한다."""

import ast
import inspect
from pathlib import Path

import kubelens.tools as tools_pkg
from kubelens.tools import ReadOnlyKube

TOOLS_DIR = Path(tools_pkg.__file__).parent
FORBIDDEN_CALL_PREFIXES = ("create_", "delete_", "patch_", "replace_", "connect_")
FORBIDDEN_IMPORTS = {"subprocess", "os.system", "kubernetes.stream", "shlex", "pty"}


def _sources() -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text(), filename=str(p))) for p in sorted(TOOLS_DIR.glob("*.py"))]


def test_no_write_or_exec_api_calls() -> None:
    for path, tree in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert not node.attr.startswith(FORBIDDEN_CALL_PREFIXES), (
                    f"{path.name}: {node.attr}"
                )
                assert "secret" not in node.attr.lower() or node.attr in {
                    "secret_key_ref",
                    "secret_ref",
                }, f"{path.name}: {node.attr}"


def test_no_shell_or_stream_imports() -> None:
    for path, tree in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {node.module or ""} | {f"{node.module}.{a.name}" for a in node.names}
            else:
                continue
            assert not names & FORBIDDEN_IMPORTS, f"{path.name}: {names}"
            assert not any(n.startswith("kubernetes.stream") for n in names), path.name


def test_readonly_kube_exposes_only_get_list_methods() -> None:
    public = [
        name
        for name, member in inspect.getmembers(ReadOnlyKube)
        if not name.startswith("_") and callable(member) and name != "from_kubeconfig"
    ]
    assert public
    for name in public:
        assert name.startswith(("list_", "read_")), name
    assert not hasattr(ReadOnlyKube, "__getattr__")


def test_readonly_kube_has_no_passthrough_attributes() -> None:
    kube = ReadOnlyKube(object(), object())
    try:
        kube.extra = 1  # type: ignore[attr-defined]
    except AttributeError:
        pass
    else:
        raise AssertionError("ReadOnlyKube must use __slots__")
