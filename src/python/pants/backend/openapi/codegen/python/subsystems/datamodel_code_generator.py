# Copyright 2024 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from pants.backend.python.subsystems.python_tool_base import PythonToolBase
from pants.backend.python.target_types import ConsoleScript
from pants.engine.rules import collect_rules


class DatamodelCodeGenerator(PythonToolBase):
    options_scope = "datamodel-code-generator"
    name = "datamodel-code-generator"
    help = "Create models from an openapi file and others (https://github.com/koxudaxi/datamodel-code-generator)."

    default_main = ConsoleScript("datamodel-codegen")
    default_requirements = ["datamodel-code-generator>=0.25.5,<0.26"]

    register_interpreter_constraints = True

    default_lockfile_resource = (
        "pants.backend.openapi.codegen.python.subsystems",
        "datamodel_code_generator.lock",
    )


def rules():
    return collect_rules()
