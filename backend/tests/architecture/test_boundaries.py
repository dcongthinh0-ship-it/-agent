"""Keep pure rules, HTTP handlers, persistence and sample code in distinct layers."""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "src/chemo_agent_product"


def test_pure_rules_and_bound_tool_dispatch_do_not_depend_on_io_frameworks():
    paths = [
        "core/domain.py",
        "modules/recommendation/matching.py",
        "modules/patient_regimen/calculations.py",
        "modules/patient_regimen/compiler.py",
        "agent_runtime/tools/dispatch.py",
    ]
    for relative in paths:
        tree = ast.parse((PACKAGE / relative).read_text())
        for node in ast.walk(tree):
            imports = []
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            assert not any(
                name.split(".")[0] in {"asyncpg", "fastapi", "httpx", "claude_agent_sdk"}
                or name.startswith("chemo_agent_product.persistence")
                or name.startswith("chemo_agent_product.integrations")
                for name in imports
            ), relative


def test_sql_execution_stays_in_repositories_and_explicit_maintenance_commands():
    for path in PACKAGE.rglob("*.py"):
        permitted = (
            path.stem == "repository"
            or path.stem.endswith("_repository")
            or "persistence" in path.relative_to(PACKAGE).parts
            or path.relative_to(PACKAGE).as_posix()
            in {"entrypoints/migrate.py", "entrypoints/word_layout.py"}
        )
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"fetch", "fetchval", "fetchrow", "execute"}:
                continue
            strings = (
                [
                    n.value
                    for n in ast.walk(node.args[0])
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                ]
                if node.args
                else []
            )
            is_sql = any(
                value.lstrip().upper().startswith(("SELECT ", "INSERT ", "UPDATE ", "DELETE "))
                for value in strings
            )
            assert not is_sql or permitted, str(path.relative_to(PACKAGE))


def test_product_package_has_no_sample_app_or_fixture_imports():
    for path in PACKAGE.rglob("*.py"):
        assert not path.stem.startswith("demo"), path
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("chemo_agent_product.demo"), path


def test_use_cases_do_not_depend_on_http_routing_or_fastapi_dependencies():
    for path in PACKAGE.rglob("*.py"):
        if path.stem not in {"service", "preparation", "bindings", "worker", "commands"}:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module != "fastapi", path
                assert module != "chemo_agent_product.core.dependencies", path
                assert not module.endswith(".api"), path


def test_runtime_prompts_are_packaged_at_the_policy_boundary():
    from chemo_agent_product.agent_runtime.policy import prompt_path

    for kind in ["RECOMMENDATION", "REVIEWER"]:
        path = prompt_path(kind)
        assert path.is_file()
        assert path.parent == PACKAGE / "agent_runtime/prompts"
        assert path.read_text().strip()
