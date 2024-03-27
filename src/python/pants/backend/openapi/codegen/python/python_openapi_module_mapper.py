# Copyright 2024 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import os
from collections import defaultdict
from typing import DefaultDict

from pants.backend.openapi.target_types import AllOpenApiDocumentTargets, OpenApiDocumentField
from pants.backend.python.dependency_inference.module_mapper import (
    FirstPartyPythonMappingImpl,
    FirstPartyPythonMappingImplMarker,
    ModuleProvider,
    ModuleProviderType,
    ResolveName,
)
from pants.backend.python.subsystems.setup import PythonSetup
from pants.backend.python.target_types import PythonResolveField
from pants.core.util_rules.stripped_source_files import StrippedFileName, StrippedFileNameRequest
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.unions import UnionRule
from pants.util.logging import LogLevel


def openapi_path_to_py_module(stripped_path: str) -> str:
    return os.path.splitext(stripped_path)[0].replace("/", ".")


# This is only used to register our implementation with the plugin hook via unions.
class PythonOpenApiMappingMarker(FirstPartyPythonMappingImplMarker):
    pass


@rule(
    desc="Creating map of OpenAPI document targets to generated Python modules",
    level=LogLevel.DEBUG,
)
async def map_openapi_documents_to_python_modules(
    openapi_targets: AllOpenApiDocumentTargets,
    python_setup: PythonSetup,
    _: PythonOpenApiMappingMarker,
) -> FirstPartyPythonMappingImpl:
    stripped_file_per_target = await MultiGet(
        Get(StrippedFileName, StrippedFileNameRequest(tgt[OpenApiDocumentField].file_path))
        for tgt in openapi_targets
    )

    resolves_to_modules_to_providers: DefaultDict[
        ResolveName, DefaultDict[str, list[ModuleProvider]]
    ] = defaultdict(lambda: defaultdict(list))
    for tgt, stripped_file in zip(openapi_targets, stripped_file_per_target):
        resolve = tgt[PythonResolveField].normalized_value(python_setup)
        module = openapi_path_to_py_module(stripped_file.value)
        resolves_to_modules_to_providers[resolve][module].append(
            ModuleProvider(tgt.address, ModuleProviderType.IMPL)
        )

    return FirstPartyPythonMappingImpl.create(resolves_to_modules_to_providers)


def rules():
    return (
        *collect_rules(),
        UnionRule(FirstPartyPythonMappingImplMarker, PythonOpenApiMappingMarker),
    )
