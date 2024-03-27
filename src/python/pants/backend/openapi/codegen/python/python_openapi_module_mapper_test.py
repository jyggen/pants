# Copyright 2024 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from textwrap import dedent

import pytest

from pants.backend.openapi.codegen.python import python_openapi_module_mapper
from pants.backend.openapi.codegen.python.extra_fields import rules as extra_fields_rules
from pants.backend.openapi.codegen.python.python_openapi_module_mapper import (
    PythonOpenApiMappingMarker,
)
from pants.backend.openapi.target_types import OpenApiDocumentGeneratorTarget
from pants.backend.openapi.target_types import rules as openapi_rules
from pants.backend.python.dependency_inference.module_mapper import (
    FirstPartyPythonMappingImpl,
    ModuleProvider,
    ModuleProviderType,
)
from pants.core.util_rules import stripped_source_files
from pants.engine.addresses import Address
from pants.testutil.rule_runner import QueryRule, RuleRunner


@pytest.fixture
def rule_runner() -> RuleRunner:
    return RuleRunner(
        rules=[
            *stripped_source_files.rules(),
            *python_openapi_module_mapper.rules(),
            *openapi_rules(),
            *extra_fields_rules(),
            QueryRule(FirstPartyPythonMappingImpl, [PythonOpenApiMappingMarker]),
        ],
        target_types=[OpenApiDocumentGeneratorTarget],
    )


def test_map_first_party_modules_to_addresses(rule_runner: RuleRunner) -> None:
    rule_runner.set_options(
        [
            "--source-root-patterns=['root1', 'root2', 'root3']",
            "--python-enable-resolves",
            "--python-resolves={'python-default': '', 'another-resolve': ''}",
        ]
    )
    rule_runner.write_files(
        {
            "root1/openapi/f1.yaml": "",
            "root1/openapi/f2.yaml": "",
            "root1/openapi/BUILD": 'openapi_documents(sources=["f1.yaml", "f2.yaml"])',
            # These OpenAPI sources will result in the same module name.
            "root1/two_owners/f.yaml": "",
            "root1/two_owners/BUILD": 'openapi_documents(sources=["f.yaml"])',
            "root2/two_owners/f.yaml": "",
            "root2/two_owners/BUILD": 'openapi_documents(sources=["f.yaml"])',
            "root1/tests/f.yaml": "",
            "root1/tests/BUILD": dedent(
                """\
                openapi_documents(
                    sources=["f.yaml"],
                    # This should be irrelevant to the module mapping because we strip source roots.
                    python_source_root='root3',
                    python_resolve='another-resolve',
                )
                """
            ),
        }
    )
    result = rule_runner.request(FirstPartyPythonMappingImpl, [PythonOpenApiMappingMarker()])

    def providers(addresses: list[Address]) -> tuple[ModuleProvider, ...]:
        return tuple(ModuleProvider(addr, ModuleProviderType.IMPL) for addr in addresses)

    assert result == FirstPartyPythonMappingImpl.create(
        {
            "python-default": {
                "openapi.f1": providers([Address("root1/openapi", relative_file_path="f1.yaml")]),
                "openapi.f2": providers([Address("root1/openapi", relative_file_path="f2.yaml")]),
                "two_owners.f": providers(
                    [
                        Address("root1/two_owners", relative_file_path="f.yaml"),
                        Address("root2/two_owners", relative_file_path="f.yaml"),
                    ]
                ),
            },
            "another-resolve": {
                "tests.f": providers([Address("root1/tests", relative_file_path="f.yaml")]),
            },
        }
    )


def test_top_level_source_root(rule_runner: RuleRunner) -> None:
    rule_runner.set_options(["--source-root-patterns=['/']", "--python-enable-resolves"])
    rule_runner.write_files(
        {"openapi/f.yaml": "", "openapi/BUILD": 'openapi_documents(sources=["f.yaml"])'}
    )
    result = rule_runner.request(FirstPartyPythonMappingImpl, [PythonOpenApiMappingMarker()])

    def providers(addresses: list[Address]) -> tuple[ModuleProvider, ...]:
        return tuple(ModuleProvider(addr, ModuleProviderType.IMPL) for addr in addresses)

    assert result == FirstPartyPythonMappingImpl.create(
        {
            "python-default": {
                "openapi.f": providers([Address("openapi", relative_file_path="f.yaml")])
            }
        }
    )
