"""Verify that the private MMD source and the public mirror stay aligned.

Run with explicit roots so the public repository never embeds a private path::

    python tools/check_public_parity.py --private-root C:\\path\\to\\MMD \
        --public-root C:\\path\\to\\mmd-public-trader

Every accepted difference must be declared in PUBLIC_PARITY_ALLOWLIST.json.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = ROOT / "PUBLIC_PARITY_ALLOWLIST.json"
SOURCE_SUFFIXES = {".py", ".js", ".html", ".css", ".json", ".bat", ".ps1", ".md", ".txt"}


@dataclass(frozen=True)
class Finding:
    classification: str
    private_path: str | None
    public_path: str | None
    detail: str


def load_allowlist(path: Path = ALLOWLIST_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def source_files(root: Path, ignored: Iterable[str]) -> set[str]:
    result = set()
    for candidate in root.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        relative = candidate.relative_to(root).as_posix()
        if not matches(relative, ignored):
            result.add(relative)
    return result


def public_name(private_name: str) -> str:
    return private_name.replace("evernus_", "mmd_")


def private_name(public_name_value: str) -> str:
    return public_name_value.replace("mmd_", "evernus_")


def normalize_text(text: str) -> str:
    """Remove only documented environment vocabulary, never application logic."""
    text = text.replace("evernus_", "mmd_").replace("EVERNUS_", "MMD_")
    text = text.replace("Evernus", "MMD").replace("evernus", "mmd")
    text = re.sub(r"https://github\.com/mdpwbe-sys/(?:mmd|mmd-public-trader)", "<REPOSITORY>", text)
    text = re.sub(r"http://127\.0\.0\.1:(?:8565|8765|8766)/callback", "<CALLBACK>", text)
    text = re.sub(r"%APPDATA%/MMD-Trader(?:/cache)?", "<STATE_PATH>", text)
    text = re.sub(r"%LOCALAPPDATA%/[^\n\"']+", "<STATE_PATH>", text)
    return text.replace("\r\n", "\n")


def compact_web_source(text: str) -> str:
    """Drop presentation-only whitespace and comments from JS/CSS/HTML source."""
    output: list[str] = []
    index, quote, escaped = 0, None, False
    while index < len(text):
        char, following = text[index], text[index + 1:index + 2]
        if quote:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"`":
            quote = char; output.append(char); index += 1; continue
        if char == "/" and following == "/":
            index = text.find("\n", index)
            if index < 0:
                break
            continue
        if char == "/" and following == "*":
            index = text.find("*/", index + 2)
            if index < 0:
                break
            index += 2
            continue
        if char.isspace():
            index += 1
            continue
        output.append(char); index += 1
    return "".join(output)


def content_fingerprint(path: Path) -> str:
    text = normalize_text(path.read_text(encoding="utf-8", errors="replace"))
    if path.suffix == ".py":
        try:
            tree = RenamePrivateSymbols().visit(ast.parse(text, filename=str(path)))
            return ast.dump(ast.fix_missing_locations(tree), include_attributes=False)
        except SyntaxError:
            return text
    if path.suffix == ".json":
        try:
            return json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"))
        except json.JSONDecodeError:
            return text
    if path.suffix in {".js", ".css", ".html"}:
        return compact_web_source(text)
    return re.sub(r"\s+", " ", text).strip()


class RenamePrivateSymbols(ast.NodeTransformer):
    def visit_Name(self, node):  # noqa: N802 - ast API name
        node.id = normalize_text(node.id)
        return node

    def visit_Attribute(self, node):  # noqa: N802 - ast API name
        self.generic_visit(node)
        node.attr = normalize_text(node.attr)
        return node

    def visit_alias(self, node):  # noqa: N802 - ast API name
        node.name = normalize_text(node.name)
        if node.asname:
            node.asname = normalize_text(node.asname)
        return node

    def visit_Constant(self, node):  # noqa: N802 - ast API name
        if isinstance(node.value, str):
            node.value = normalize_text(node.value)
        return node


def api_methods(path: Path) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    api = next((item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "Api"), None)
    if api is None:
        raise ValueError(f"Api class not found in {path}")
    return {item.name: item for item in api.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}


def method_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    node = RenamePrivateSymbols().visit(ast.fix_missing_locations(node))
    return ast.dump(node, include_attributes=False)


def method_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int | None]:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    if positional and positional[0].arg == "self":
        positional = positional[1:]
    minimum = max(0, len(positional) - len(args.defaults))
    maximum = None if args.vararg else len(positional)
    return minimum, maximum


def _balanced_arguments(source: str, opening_paren: int) -> tuple[int, int]:
    """Return (argument_count, closing_index) for a JS call starting at ``("`."""
    depth, index, count = 1, opening_paren + 1, 0
    saw_value, quote, escaped = False, None, False
    while index < len(source):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"`":
            quote = char; saw_value = True; index += 1; continue
        if char == "(":
            depth += 1; saw_value = True
        elif char == ")":
            depth -= 1
            if depth == 0:
                return (count + 1 if saw_value else 0), index
        elif char == "," and depth == 1:
            count += 1; saw_value = False
        elif not char.isspace():
            saw_value = True
        index += 1
    raise ValueError("unterminated JavaScript call")


DIRECT_API_CALL = re.compile(
    r"(?:(?:\bapi\s*\(\)\s*\??)|(?:\b(?:api|a)\s*\??)|(?:\bgetApi\s*\(\)\s*\??)|(?:window\.pywebview\.api\s*\??))\.\s*(?P<name>[A-Za-z_]\w*)\s*\("
)
WRAPPED_API_CALL = re.compile(r"\bcall\s*\(\s*['\"](?P<name>[A-Za-z_]\w*)['\"](?P<rest>\s*,)?")


def js_api_calls(root: Path) -> list[tuple[str, int, str, int]]:
    """Collect direct and ``call('method', ...)`` pywebview calls without a JS dependency."""
    calls: list[tuple[str, int, str, int]] = []
    for path in [*root.glob("gui/*.js"), root / "gui" / "index.html"]:
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        for match in DIRECT_API_CALL.finditer(source):
            count, _ = _balanced_arguments(source, match.end() - 1)
            calls.append((path.relative_to(root).as_posix(), source.count("\n", 0, match.start()) + 1, match.group("name"), count))
        for match in WRAPPED_API_CALL.finditer(source):
            if not match.group("rest"):
                count = 0
            else:
                count, _ = _balanced_arguments(source, source.find("(", match.start()))
                count -= 1  # First argument is the literal API method name.
            calls.append((path.relative_to(root).as_posix(), source.count("\n", 0, match.start()) + 1, match.group("name"), count))
    return calls


def contract_findings(root: Path, gui_module: str) -> list[Finding]:
    methods = api_methods(root / gui_module)
    findings: list[Finding] = []
    for path, line, name, argument_count in js_api_calls(root):
        method = methods.get(name)
        if method is None:
            findings.append(Finding("PUBLIC_ONLY_BUG", None, path, f"JS calls missing Api.{name} at line {line}"))
            continue
        minimum, maximum = method_signature(method)
        if argument_count < minimum or (maximum is not None and argument_count > maximum):
            findings.append(Finding("PUBLIC_ONLY_BUG", None, path, f"Api.{name} receives {argument_count} argument(s) at line {line}; signature allows {minimum}..{maximum if maximum is not None else '∞'}"))
    return findings


def expected_content_difference(private_path: str, public_path: str, allowlist: dict) -> dict | None:
    for entry in allowlist.get("content_differences", []):
        if entry["private"] == private_path and entry["public"] == public_path:
            return entry
    return None


def compare_api(private_root: Path, public_root: Path, allowlist: dict) -> list[Finding]:
    private_methods = api_methods(private_root / "evernus_gui.py")
    public_methods = api_methods(public_root / "mmd_gui.py")
    exceptions = allowlist.get("api_method_exceptions", {})
    findings: list[Finding] = []
    for name in sorted(set(private_methods) | set(public_methods)):
        exception = exceptions.get(name)
        if name not in private_methods:
            if exception:
                continue
            findings.append(Finding("PUBLIC_ONLY_BUG", None, "mmd_gui.py", f"Api.{name} exists only in public"))
        elif name not in public_methods:
            if exception:
                continue
            findings.append(Finding("UNINTENTIONAL_DIVERGENCE", "evernus_gui.py", None, f"Api.{name} is missing from public"))
        elif method_fingerprint(private_methods[name]) != method_fingerprint(public_methods[name]) and not exception:
            findings.append(Finding("UNINTENTIONAL_DIVERGENCE", "evernus_gui.py", "mmd_gui.py", f"Api.{name} implementation differs"))
    return findings


def compare_files(private_root: Path, public_root: Path, allowlist: dict) -> list[Finding]:
    ignored = allowlist.get("ignore_paths", [])
    private_files, public_files = source_files(private_root, ignored), source_files(public_root, ignored)
    only_private = set(allowlist.get("only_private", []))
    only_public = set(allowlist.get("only_public", []))
    findings: list[Finding] = []
    for path in sorted(private_files):
        public_path = public_name(path)
        if public_path not in public_files:
            classification = "PRIVATE_ONLY" if matches(path, only_private) else "UNINTENTIONAL_DIVERGENCE"
            findings.append(Finding(classification, path, None, "file missing from public mirror"))
    for path in sorted(public_files):
        private_path = private_name(path)
        if private_path not in private_files:
            classification = "PUBLIC_ONLY" if matches(path, only_public) else "PUBLIC_ONLY_BUG"
            findings.append(Finding(classification, None, path, "file has no private counterpart"))
    for private_path in sorted(private_files):
        public_path = public_name(private_path)
        if public_path not in public_files:
            continue
        private_text = content_fingerprint(private_root / private_path)
        public_text = content_fingerprint(public_root / public_path)
        if private_text == public_text:
            continue
        allowed = expected_content_difference(private_path, public_path, allowlist)
        if allowed:
            findings.append(Finding(allowed["classification"], private_path, public_path, allowed["reason"]))
        else:
            findings.append(Finding("UNINTENTIONAL_DIVERGENCE", private_path, public_path, "normalized contents differ"))
    return findings


def report(findings: list[Finding]) -> None:
    print("classification\tprivate\tpublic\tdetail")
    for finding in findings:
        print(f"{finding.classification}\t{finding.private_path or '-'}\t{finding.public_path or '-'}\t{finding.detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--public-root", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, default=ALLOWLIST_PATH)
    args = parser.parse_args(argv)
    allowlist = load_allowlist(args.allowlist)
    findings = compare_files(args.private_root, args.public_root, allowlist)
    findings.extend(compare_api(args.private_root, args.public_root, allowlist))
    findings.extend(contract_findings(args.private_root, "evernus_gui.py"))
    findings.extend(contract_findings(args.public_root, "mmd_gui.py"))
    report(findings)
    failures = [finding for finding in findings if finding.classification in {"PUBLIC_ONLY_BUG", "UNINTENTIONAL_DIVERGENCE"}]
    print(f"\n{len(findings)} documented/observed difference(s), {len(failures)} blocking.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
