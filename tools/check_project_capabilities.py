"""Check maintained capabilities against source limits and generated documentation.

This reads source text/AST; it never imports a compiler, launches tools, cooks
content, or changes the capability declarations. --write updates marked docs.
"""
from __future__ import annotations

import argparse
import ast
import json
import operator
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BEGIN = "<!-- project-capabilities:begin -->"
END = "<!-- project-capabilities:end -->"
STATES = {"implemented", "limited", "unavailable", "pending_validation"}
REQUIRED = {"full-authoring-preview", "ordinary-native-play", "streamed-native-play",
            "rom-play", "static-lighting", "character-animation", "streamed-animation",
            "runtime-regions", "hardware-validation"}


def load_capabilities(root=ROOT, path=None):
    return json.loads((Path(path) if path else Path(root) / "tools/project_capabilities.json").read_text(encoding="utf-8"))


def _path(root, relative):
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"Source/document path must be project relative: {relative}")
    resolved = (Path(root) / candidate).resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise ValueError(f"Path escapes project: {relative}")
    return resolved


def _number(expression, lookup=lambda name: None):
    tree = ast.parse(expression, mode="eval")
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
              ast.LShift: operator.lshift, ast.RShift: operator.rshift}
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.Name):
            value = lookup(node.id)
            if value is None:
                raise ValueError(f"Unresolved numeric name: {node.id}")
            return value
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            return binary[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        raise ValueError("Unsupported numeric expression")
    return visit(tree.body)


def source_value(root, reference):
    text = _path(root, reference["path"]).read_text(encoding="utf-8")
    symbol = reference["symbol"]
    if not re.search(r"\b" + re.escape(symbol) + r"\b", text):
        raise ValueError(f"Missing symbol {symbol}")
    kind = reference.get("kind")
    if kind is None:
        return None
    if kind == "pattern":
        if reference["path"].endswith(".py"):
            functions = [n for n in ast.parse(text).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == symbol]
            if len(functions) != 1:
                raise ValueError(f"Missing Python function {symbol}")
            text = ast.get_source_segment(text, functions[0])
        matches = re.findall(reference["pattern"], text)
        if len(matches) != 1 or not isinstance(matches[0], str):
            raise ValueError(f"Expected one numeric capture for {symbol}")
        return _number(matches[0])
    if kind == "c_constant":
        def find(name, visited=()):
            if name in visited:
                raise ValueError(f"C constant cycle at {name}")
            define = re.search(r"^\s*#define\s+" + re.escape(name) + r"\s+([^\n]+)", text, re.M)
            enum = re.search(r"\b" + re.escape(name) + r"\s*=\s*([^,}\n]+)", text)
            match = define or enum
            if not match:
                raise ValueError(f"Missing numeric C constant {name}")
            expr = re.sub(r"/\*.*?\*/|//.*", "", match[1]).strip()
            expr = re.sub(r"(?<=\d)[uUlL]+\b", "", expr)
            return _number(expr, lambda key: find(key, (*visited, name)))
        return find(symbol)
    module = ast.parse(text)
    nodes = module.body
    if kind == "python_dict":
        functions = [n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == symbol]
        if len(functions) != 1:
            raise ValueError(f"Missing Python function {symbol}")
        nodes = list(ast.walk(functions[0]))
        target = reference["variable"]
    elif kind == "python_constant":
        target = symbol
    else:
        raise ValueError(f"Unsupported source reference kind: {kind}")
    assignments = [n.value for n in nodes if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == target for t in n.targets)]
    if len(assignments) != 1:
        raise ValueError(f"Expected one Python assignment for {target}")
    value = assignments[0]
    if "key" in reference:
        key = reference["key"]
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict":
            fields = {n.arg: n.value for n in value.keywords}
        elif isinstance(value, ast.Dict):
            fields = {ast.literal_eval(k): v for k, v in zip(value.keys, value.values)}
        else:
            raise ValueError(f"Expected numeric dictionary {target}")
        value = fields[key]
    return _number(ast.unparse(value))


def validate(data, root=ROOT):
    errors = []
    if type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        errors.append("Unsupported capability schema_version")
    rows = data.get("capabilities", [])
    ids = [row.get("id") for row in rows]
    if len(set(ids)) != len(ids) or REQUIRED - set(ids):
        errors.append("Capability IDs must be unique and include all preview/runtime categories")
    limits = data.get("limits", {})
    for row in rows:
        if row.get("state") not in STATES or not row.get("label") or not row.get("description"):
            errors.append(f"Invalid capability row: {row.get('id')}")
        if set(row.get("limits", [])) - set(limits):
            errors.append(f"Unknown limit on {row.get('id')}")
        if not row.get("source_references"):
            errors.append(f"Missing source references on {row.get('id')}")
        for ref in row.get("source_references", []):
            try:
                source_value(root, ref)
            except (ValueError, OSError, KeyError, SyntaxError) as error:
                errors.append(f"{row.get('id')}: {error}")
    for name, limit in limits.items():
        if type(limit.get("value")) not in (int, float) or not limit.get("unit") or not limit.get("scope"):
            errors.append(f"Invalid numeric limit: {name}")
        refs = limit.get("source_references", [])
        if not refs or any(not ref.get("kind") for ref in refs):
            errors.append(f"Numeric limit needs checked source references: {name}")
        for ref in refs:
            try:
                actual = source_value(root, ref)
                if actual != limit.get("value"):
                    errors.append(f"{name}: declared {limit.get('value')}, source {ref['path']}:{ref['symbol']} is {actual}")
            except (ValueError, OSError, KeyError, SyntaxError) as error:
                errors.append(f"{name}: {error}")
    for document in data.get("documentation", []):
        if document.get("section") not in ("summary", "matrix", "limits"):
            errors.append(f"Invalid documentation section: {document}")
        if document.get("section") == "limits" and (not document.get("limits") or set(document["limits"]) - set(limits)):
            errors.append(f"Unknown or missing documentation limits: {document}")
        try:
            _path(root, document["path"])
        except (ValueError, KeyError) as error:
            errors.append(str(error))
    return errors


def render_section(data, document):
    prefix = "../" * (len(Path(document["path"]).parts) - 1)
    lines = [BEGIN, "This section is maintained in [project_capabilities.json](" + prefix +
             "tools/project_capabilities.json); run `python tools/check_project_capabilities.py` to check it.", ""]
    if document["section"] == "summary":
        selected = {"full-authoring-preview", "ordinary-native-play", "streamed-native-play", "rom-play", "streamed-animation", "hardware-validation"}
        states = "; ".join(row["label"] + ": " + row["state"].replace("_", " ") for row in data["capabilities"] if row["id"] in selected)
        lines.append(states + ". Streamed missions use DLM1 directory version " + str(data["limits"]["mission_directory_version"]["value"]) +
                     " and DLR1 chunks through version " + str(data["limits"]["mission_chunk_version"]["value"]) +
                     ". Authoring, host simulation and ROM residency have different limits. See the [capability table and project status](" + prefix + "docs/project-status.md).")
    else:
        if document["section"] == "matrix":
            lines += ["| Capability | State | Current behavior and limits |", "| --- | --- | --- |"]
            for row in data["capabilities"]:
                lines.append(f"| {row['label']} | {row['state'].replace('_', ' ')} | {row['description']} |")
        lines += ["", "These are software validation/storage limits, not measured capacity for every workload. Host authoring budgets do not certify ROM residency or frame time.", "", "| Limit | Value | Scope | Source |", "| --- | ---: | --- | --- |"]
        for name, limit in data["limits"].items():
            if document["section"] == "limits" and name not in document["limits"]:
                continue
            sources = ", ".join(f"[{r['symbol']}]({prefix}{r['path']})" for r in limit["source_references"])
            lines.append(f"| `{name}` | {limit['value']:,} {limit['unit']} | {limit['scope']} | {sources} |")
    return "\n".join([*lines, END])


def check_documents(data, root=ROOT, write=False):
    errors = []
    for document in data.get("documentation", []):
        path = _path(root, document["path"])
        current = path.read_text(encoding="utf-8")
        if current.count(BEGIN) != 1 or current.count(END) != 1 or current.index(BEGIN) > current.index(END):
            errors.append(f"{document['path']}: expected exactly one ordered capability marker pair")
            continue
        start, end = current.index(BEGIN), current.index(END) + len(END)
        expected = render_section(data, document)
        if current[start:end] != expected:
            if write:
                path.write_text(current[:start] + expected + current[end:], encoding="utf-8")
            else:
                errors.append(f"{document['path']}: capability section is stale; use --write")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT)
    parser.add_argument("--write", action="store_true", help="Update marked documentation after source checks pass")
    args = parser.parse_args(argv)
    data = load_capabilities(args.project)
    errors = validate(data, args.project)
    if not errors:
        errors = check_documents(data, args.project, args.write)
    print(json.dumps({"schema_version": 1, "passed": not errors, "capabilities": len(data.get("capabilities", [])),
                      "limits": len(data.get("limits", {})), "errors": errors}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
