# Copyright 2024 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from pants.backend.openapi.target_types import OpenApiDocumentGeneratorTarget, OpenApiDocumentTarget
from pants.backend.python.target_types import PythonResolveField
from pants.engine.target import BoolField, StringField
from pants.util.strutil import help_text


class PythonSourceResolveField(PythonResolveField):
    alias = "python_resolve"


class PythonSourceRootField(StringField):
    alias = "python_source_root"
    help = help_text(
        """
        The source root to generate Python sources under.
        If unspecified, the source root the `openapi_document` is under will be used.
        """
    )


class PythonSkipField(BoolField):
    alias = "skip_python"
    default = False
    help = "If true, skips generation of Python sources from this target"


def rules():
    return [
        OpenApiDocumentTarget.register_plugin_field(PythonSourceResolveField),
        OpenApiDocumentGeneratorTarget.register_plugin_field(PythonSourceResolveField),
        OpenApiDocumentTarget.register_plugin_field(PythonSkipField),
        OpenApiDocumentGeneratorTarget.register_plugin_field(PythonSkipField),
        OpenApiDocumentTarget.register_plugin_field(PythonSourceRootField),
        OpenApiDocumentGeneratorTarget.register_plugin_field(PythonSourceRootField),
    ]
