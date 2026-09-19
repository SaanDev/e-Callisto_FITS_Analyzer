"""Check import, resource and freezer connections after moving feature packages."""

import ast
import importlib.util
from pathlib import Path
import re
import runpy
from types import SimpleNamespace

import pytest

from src.core.paths import find_startup_logo_path, project_base_path, source_main_path


ROOT = Path(__file__).resolve().parents[2]


def test_all_application_imports_resolve():
    modules = {"src"}
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src."):
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names if alias.name.startswith("src."))
    missing = sorted(module for module in modules if importlib.util.find_spec(module) is None)
    assert not missing, missing


def test_documentation_links_point_at_existing_files():
    """The guides cross-reference moved files, so a dropped one must not go unnoticed.

    ``docs/build/`` in particular was ignored by an unanchored ``build/`` rule in
    ``.gitignore``, which silently dropped the macOS guide it links to.
    """
    link = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
    documents = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    broken = []
    for document in documents:
        for target in link.findall(document.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            relative = target.split("#", 1)[0]
            if relative and not (document.parent / relative).exists():
                broken.append(f"{document.relative_to(ROOT).as_posix()} -> {target}")
    assert not broken, broken


def test_source_resources_resolve_outside_checkout(monkeypatch, tmp_path):
    from src.ui.common.gui_shared import resource_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.frozen", False, raising=False)
    assert source_main_path() == ROOT / "src" / "main.py"
    assert source_main_path().is_file()
    base = project_base_path(module_file=__file__, frozen=False)
    assert Path(base) == ROOT
    assert Path(find_startup_logo_path(base_path=base)) == ROOT / "assets/branding/FITS_analyzer.png"
    for relative in ("assets/icons/open.svg", "assets/band_splitting_icons/light/add_points.svg"):
        assert Path(resource_path(relative)).is_file()


def test_helper_commands_use_existing_source_launcher(monkeypatch):
    from src.ui.app.gui_workers import GoesOverlayLoadWorker
    from src.ui.cme.cme_launcher import build_cme_helper_command

    monkeypatch.setattr("sys.frozen", False, raising=False)
    command = build_cme_helper_command("https://example.com/movie.mpg")
    assert Path(command[1]) == source_main_path()
    # _helper_command does not depend on worker state or start any processes.
    command = GoesOverlayLoadWorker._helper_command(None, "request.json", "response.json")
    assert Path(command[1]) == source_main_path()


@pytest.mark.parametrize("spec_name", ["FITS_Analyzer.spec", "FITS_Analyzer_win.spec", "FITS_Analyzer_linux.spec"])
def test_pyinstaller_inputs_exist(spec_name):
    spec = ROOT / "packaging/pyinstaller" / spec_name
    captured = {}

    def analysis(scripts, **options):
        captured.update(options)
        assert scripts == [str(source_main_path())]
        return SimpleNamespace(pure=[], zipped_data=[], scripts=[], binaries=[], datas=[])

    def executable(*args, **options):
        assert Path(options["icon"]).is_file()
        return SimpleNamespace()

    runpy.run_path(str(spec), init_globals={
        "SPECPATH": str(spec.parent),
        "Analysis": analysis,
        "PYZ": lambda *args, **kwargs: None,
        "EXE": executable,
        "COLLECT": lambda *args, **kwargs: None,
    })
    for source, destination in captured["datas"]:
        assert Path(source).exists(), (source, destination)
    assert "assets/branding" in {destination for _, destination in captured["datas"]}
    for hook in captured["hookspath"] + captured["runtime_hooks"]:
        assert Path(hook).exists(), hook
    for module in captured["hiddenimports"]:
        if module == "src" or module.startswith("src."):
            assert importlib.util.find_spec(module) is not None, module
