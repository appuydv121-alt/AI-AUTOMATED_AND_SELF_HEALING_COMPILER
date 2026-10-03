"""tree-sitter front-end for C++ source analysis.

Extracts functions, scopes, declarations (with initializer status),
and read/write sites.  Falls back to ``cpp_declarations`` when
tree-sitter is unavailable.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# tree-sitter is optional — degrade gracefully
try:
    import tree_sitter_cpp as _tscpp
    from tree_sitter import Language, Parser

    _CPP_LANG = Language(_tscpp.language())
    _PARSER = Parser(_CPP_LANG)
    TREE_SITTER_AVAILABLE = True
except Exception:
    TREE_SITTER_AVAILABLE = False


def parse_source(source: str) -> dict[str, Any]:
    """Return an AST summary with functions, scopes, declarations, and usages.

    Returns a dict with keys: ``functions``, ``scopes``, ``declarations``,
    ``usages``, ``frontend_used``.
    """
    if TREE_SITTER_AVAILABLE:
        return _parse_with_tree_sitter(source)
    return _parse_fallback(source)


# ── tree-sitter implementation ─────────────────────────────────────────────

def _parse_with_tree_sitter(source: str) -> dict[str, Any]:
    tree = _PARSER.parse(source.encode("utf-8"))
    root = tree.root_node

    functions: list[dict[str, Any]] = []
    declarations: list[dict[str, Any]] = []
    usages: list[dict[str, Any]] = []
    scopes: list[dict[str, Any]] = []

    _walk(root, functions, declarations, usages, scopes, source_bytes=source.encode("utf-8"))

    return {
        "functions": functions,
        "scopes": scopes,
        "declarations": declarations,
        "usages": usages,
        "frontend_used": "tree-sitter",
    }


def _walk(
    node,
    functions: list,
    declarations: list,
    usages: list,
    scopes: list,
    source_bytes: bytes,
    scope_id: int = 0,
    depth: int = 0,
):
    """Recursive AST walker."""
    # Function definitions
    if node.type == "function_definition":
        declarator = node.child_by_field_name("declarator")
        name = _extract_function_name(declarator)
        body = node.child_by_field_name("body")
        fn_info = {
            "name": name or "(anonymous)",
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "params": _extract_params(declarator),
        }
        functions.append(fn_info)

    # Compound statements create scopes
    if node.type == "compound_statement":
        scope_id += 1
        scopes.append({
            "id": scope_id,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "depth": depth,
        })
        depth += 1

    # Variable declarations
    if node.type == "declaration":
        _extract_declarations(node, declarations, scope_id, source_bytes)

    # For-loop init declarations
    if node.type == "for_statement":
        init = node.child_by_field_name("initializer")
        if init and init.type == "declaration":
            # This declaration is scoped to the for-loop body
            for_scope = scope_id + 1  # will get its own scope from the compound_statement
            _extract_declarations(init, declarations, for_scope, source_bytes)

    # Identifier usages (reads/writes)
    if node.type == "identifier" and node.parent:
        parent = node.parent
        ident_name = node.text.decode("utf-8") if isinstance(node.text, bytes) else str(node.text)
        line = node.start_point[0] + 1

        # Determine if this is a write or a read
        is_write = False
        if parent.type == "assignment_expression":
            left = parent.child_by_field_name("left")
            if left and left.id == node.id:
                is_write = True
        elif parent.type in ("update_expression", "compound_assignment_expr"):
            is_write = True
        elif parent.type == "argument_list":
            # Could be a read or write (pass by reference) — treat as read
            pass

        usages.append({
            "name": ident_name,
            "line": line,
            "kind": "write" if is_write else "read",
            "scope_id": scope_id,
        })

    for child in node.children:
        _walk(child, functions, declarations, usages, scopes, source_bytes, scope_id, depth)


def _extract_function_name(declarator) -> Optional[str]:
    """Drill into nested declarators to find the function name."""
    if declarator is None:
        return None
    if declarator.type == "function_declarator":
        inner = declarator.child_by_field_name("declarator")
        if inner and inner.type == "identifier":
            text = inner.text
            return text.decode("utf-8") if isinstance(text, bytes) else str(text)
        if inner:
            return _extract_function_name(inner)
    if declarator.type == "identifier":
        text = declarator.text
        return text.decode("utf-8") if isinstance(text, bytes) else str(text)
    # pointer/reference declarators
    for child in declarator.children:
        result = _extract_function_name(child)
        if result:
            return result
    return None


def _extract_params(declarator) -> list[dict[str, str]]:
    """Extract parameter names and types from a function declarator."""
    params: list[dict[str, str]] = []
    if declarator is None:
        return params
    if declarator.type == "function_declarator":
        param_list = declarator.child_by_field_name("parameters")
        if param_list:
            for child in param_list.children:
                if child.type == "parameter_declaration":
                    ptype_node = child.child_by_field_name("type")
                    pdecl_node = child.child_by_field_name("declarator")
                    ptype = (ptype_node.text.decode("utf-8") if ptype_node and isinstance(ptype_node.text, bytes)
                             else str(ptype_node.text) if ptype_node else "")
                    pname = ""
                    if pdecl_node:
                        pname = (pdecl_node.text.decode("utf-8") if isinstance(pdecl_node.text, bytes)
                                 else str(pdecl_node.text))
                    params.append({"type": ptype, "name": pname})
    return params


def _extract_declarations(node, declarations: list, scope_id: int, source_bytes: bytes):
    """Extract variable declarations from a declaration node."""
    type_node = node.child_by_field_name("type")
    decl_type = ""
    if type_node:
        decl_type = (type_node.text.decode("utf-8") if isinstance(type_node.text, bytes)
                     else str(type_node.text))

    for child in node.children:
        if child.type in ("init_declarator", "identifier"):
            if child.type == "init_declarator":
                name_node = child.child_by_field_name("declarator")
                value_node = child.child_by_field_name("value")
                has_init = value_node is not None
            else:
                name_node = child
                has_init = False

            if name_node:
                raw = name_node.text
                name = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                # Strip array suffixes like [5]
                clean_name = re.sub(r"\[.*\]", "", name).strip()
                if clean_name:
                    declarations.append({
                        "name": clean_name,
                        "type": decl_type,
                        "line": name_node.start_point[0] + 1,
                        "has_initializer": has_init,
                        "scope_id": scope_id,
                    })


# ── Fallback (no tree-sitter) ──────────────────────────────────────────────

def _parse_fallback(source: str) -> dict[str, Any]:
    """Minimal extraction using regex heuristics — much less accurate."""
    try:
        from cpp_declarations import extract_declarations
        raw_decls = extract_declarations(source)
        declarations = []
        for d in raw_decls:
            declarations.append({
                "name": d.name,
                "type": d.declared_type,
                "line": d.line,
                "has_initializer": d.initializer is not None and d.initializer != "",
                "scope_id": 0,
            })
        return {
            "functions": [],
            "scopes": [],
            "declarations": declarations,
            "usages": [],
            "frontend_used": "cpp_declarations",
        }
    except Exception:
        return {
            "functions": [],
            "scopes": [],
            "declarations": [],
            "usages": [],
            "frontend_used": "none",
        }
