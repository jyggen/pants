# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).
# -*- coding: utf-8 -*-

# NB: This must be compatible with Python 2.7 and 3.5+.
# NB: If you're needing to debug this, an easy way is to just invoke it on a file.
#   E.g. `MIN_DOTS=1 STRING_IMPORTS=N python3 src/python/pants/backend/python/dependency_inference/scripts/import_parser.py FILENAME`

from __future__ import print_function, unicode_literals

import ast
import itertools
import json
import os
import re
import sys
import tokenize
from io import open

MIN_DOTS = os.environ["MIN_DOTS"]

# This regex is used to infer imports from strings, e.g.
#  `importlib.import_module("example.subdir.Foo")`.
STRING_IMPORT_REGEX = re.compile(
    r"^([a-z_][a-z_\d]*\.){" + MIN_DOTS + r",}[a-zA-Z_]\w*$",
    re.UNICODE,
)


class AstVisitor(ast.NodeVisitor):
    def __init__(self, package_parts, contents):
        self._package_parts = package_parts
        self._contents_lines = contents.decode(errors="ignore").splitlines()
        # N.B. use `setdefault` when adding imports
        self.imports = {}  # maps module_name to first lineno of occurance

    def maybe_add_string_import(self, node, s):
        if STRING_IMPORT_REGEX.match(s):
            self.imports.setdefault(s, node.lineno)

    @staticmethod
    def _is_pragma_ignored(line):
        return "# pants: ignore" in line

    def _visit_import_stmt(self, node, import_prefix):
        # N.B. We only add imports whose line doesn't contain "# pants: ignore"
        # However, `ast` doesn't expose the exact lines each specific import is on,
        # so we are forced to tokenize the import statement to tease out which imported
        # name is on which line so we can check for the ignore pragma.
        node_lines_iter = itertools.islice(self._contents_lines, node.lineno - 1, None)
        token_iter = tokenize.generate_tokens(lambda: next(node_lines_iter))

        def consume_until(string):
            for token in token_iter:
                if token[1] == string:
                    return token

        consume_until("import")

        # N.B. The names in this list are in the same order as the import statement
        for alias in node.names:
            token = consume_until(alias.name.split(".")[-1])

            # N.B. Keep consuming lines while they end in a line-continuation
            #   (unfortunately `tokenize` doesn't capture this)
            while token[4].endswith("\\"):
                token = next(token_iter)

            if not self._is_pragma_ignored(token[4]):
                self.imports.setdefault(import_prefix + alias.name, token[3][0] + node.lineno - 1)
            if alias.asname and token[1] != alias.asname:
                consume_until(alias.asname)

    def visit_Import(self, node):
        self._visit_import_stmt(node, "")

    def visit_ImportFrom(self, node):
        if node.level:
            # Relative import.
            rel_module = node.module
            abs_module = ".".join(
                self._package_parts[0 : len(self._package_parts) - node.level + 1]
                + ([] if rel_module is None else [rel_module])
            )
        else:
            abs_module = node.module
        self._visit_import_stmt(node, abs_module + ".")

    def visit_Call(self, node):
        # Handle __import__("string_literal").  This is commonly used in __init__.py files,
        # to explicitly mark namespace packages.  Note that we don't handle more complex
        # uses, such as those that set `level`.
        if isinstance(node.func, ast.Name) and node.func.id == "__import__" and len(node.args) == 1:
            name = None
            if sys.version_info[0:2] < (3, 8) and isinstance(node.args[0], ast.Str):
                name = node.args[0].s
            elif isinstance(node.args[0], ast.Constant):
                name = str(node.args[0].value)

            if name is not None:
                lineno = node.args[0].lineno
                if not self._is_pragma_ignored(self._contents_lines[lineno - 1]):
                    self.imports.setdefault(name, lineno)
                return

        self.generic_visit(node)


if os.environ["STRING_IMPORTS"] == "y":
    # String handling changes a bit depending on Python version. We dynamically add the appropriate
    # logic.
    if sys.version_info[0:2] == (2, 7):

        def visit_Str(self, node):
            try:
                val = node.s.decode("utf8") if isinstance(node.s, bytes) else node.s
                self.maybe_add_string_import(node, val)
            except UnicodeError:
                pass

        setattr(AstVisitor, "visit_Str", visit_Str)

    elif sys.version_info[0:2] < (3, 8):

        def visit_Str(self, node):
            self.maybe_add_string_import(node, node.s)

        setattr(AstVisitor, "visit_Str", visit_Str)

    else:

        def visit_Constant(self, node):
            if isinstance(node.value, str):
                self.maybe_add_string_import(node, node.value)

        setattr(AstVisitor, "visit_Constant", visit_Constant)


def main(filename):
    with open(filename, "rb") as f:
        content = f.read()
    try:
        tree = ast.parse(content, filename=filename)
    except SyntaxError:
        return

    package_parts = os.path.dirname(filename).split(os.path.sep)
    visitor = AstVisitor(package_parts, content)
    visitor.visit(tree)

    # We have to be careful to set the encoding explicitly and write raw bytes ourselves.
    # See below for where we explicitly decode.
    buffer = sys.stdout if sys.version_info[0:2] == (2, 7) else sys.stdout.buffer
    output = json.dumps(visitor.imports)
    buffer.write(output.encode("utf8"))


if __name__ == "__main__":
    main(sys.argv[1])