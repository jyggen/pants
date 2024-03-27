# Copyright 2024 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from pants.backend.openapi.codegen.python import (
    python_openapi_module_mapper,
)
from pants.backend.openapi.codegen.python.extra_fields import (
    rules as extra_fields_rules,
)
from pants.backend.openapi.codegen.python.rules import (
    rules as codegen_rules,
)
from pants.backend.openapi.codegen.python.subsystems.datamodel_code_generator import (
    rules as subsystem_rules,
)

from pants.backend.experimental.openapi.register import rules as openapi_rules
from pants.backend.experimental.openapi.register import target_types as openapi_target_types


def target_types():
    return [*openapi_target_types()]


def rules():
    return [
        *openapi_rules(),
        *codegen_rules(),
        *extra_fields_rules(),
        *python_openapi_module_mapper.rules(),
        *subsystem_rules(),
    ]
