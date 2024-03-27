# Copyright 2024 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

from textwrap import dedent
from typing import Iterable

import pytest

from pants.backend.openapi.codegen.python import python_openapi_module_mapper
from pants.backend.openapi.codegen.python.extra_fields import rules as extra_fields_rules
from pants.backend.openapi.codegen.python.rules import GeneratePythonFromOpenApiRequest
from pants.backend.openapi.codegen.python.rules import rules as python_codegen_rules
from pants.backend.openapi.codegen.python.subsystems.datamodel_code_generator import (
    rules as subsystem_rules,
)
from pants.backend.openapi.sample.resources import PETSTORE_SAMPLE_SPEC
from pants.backend.openapi.target_types import (
    OpenApiDocumentDependenciesField,
    OpenApiDocumentField,
    OpenApiDocumentGeneratorTarget,
    OpenApiDocumentTarget,
    OpenApiSourceGeneratorTarget,
    OpenApiSourceTarget,
)
from pants.backend.openapi.target_types import rules as target_types_rules
from pants.backend.python.target_types import PythonSourcesGeneratorTarget, PythonSourceTarget
from pants.backend.python.target_types_rules import rules as python_backend_rules
from pants.engine.addresses import Address, Addresses
from pants.engine.target import (
    Dependencies,
    DependenciesRequest,
    GeneratedSources,
    HydratedSources,
    HydrateSourcesRequest,
)
from pants.testutil.rule_runner import PYTHON_BOOTSTRAP_ENV, QueryRule, RuleRunner


@pytest.fixture
def rule_runner() -> RuleRunner:
    rule_runner = RuleRunner(
        target_types=[
            PythonSourceTarget,
            PythonSourcesGeneratorTarget,
            OpenApiSourceTarget,
            OpenApiSourceGeneratorTarget,
            OpenApiDocumentTarget,
            OpenApiDocumentGeneratorTarget,
        ],
        rules=[
            *python_backend_rules(),
            *python_codegen_rules(),
            *python_openapi_module_mapper.rules(),
            *extra_fields_rules(),
            *target_types_rules(),
            *subsystem_rules(),
            QueryRule(HydratedSources, (HydrateSourcesRequest,)),
            QueryRule(GeneratedSources, (GeneratePythonFromOpenApiRequest,)),
            QueryRule(Addresses, (DependenciesRequest,)),
        ],
    )
    rule_runner.set_options(args=[], env_inherit=PYTHON_BOOTSTRAP_ENV)
    return rule_runner


def _assert_generated_files(
    rule_runner: RuleRunner,
    address: Address,
    *,
    expected_files: Iterable[str],
    source_roots: Iterable[str] | None = None,
    extra_args: Iterable[str] = (),
) -> None:
    args = []
    if source_roots:
        args.append(f"--source-root-patterns={repr(source_roots)}")
    args.extend(extra_args)
    rule_runner.set_options(args, env_inherit=PYTHON_BOOTSTRAP_ENV)

    tgt = rule_runner.get_target(address)
    protocol_sources = rule_runner.request(
        HydratedSources, [HydrateSourcesRequest(tgt[OpenApiDocumentField])]
    )

    generated_sources = rule_runner.request(
        GeneratedSources, [GeneratePythonFromOpenApiRequest(protocol_sources.snapshot, tgt)]
    )

    assert set(generated_sources.snapshot.files) == set(expected_files)


def test_skip_generate_python(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "BUILD": "openapi_document(name='petstore', source='petstore_spec.yaml', skip_python=True)",
            "petstore_spec.yaml": PETSTORE_SAMPLE_SPEC,
        }
    )

    def assert_gen(address: Address, expected: Iterable[str]) -> None:
        _assert_generated_files(rule_runner, address, expected_files=expected)

    tgt_address = Address("", target_name="petstore")
    assert_gen(tgt_address, [])

    tgt = rule_runner.get_target(tgt_address)
    runtime_dependencies = rule_runner.request(
        Addresses, [DependenciesRequest(tgt[OpenApiDocumentDependenciesField])]
    )
    assert not runtime_dependencies


def test_generate_python_sources(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "src/openapi/BUILD": "openapi_document(name='petstore', source='petstore_spec.yaml')",
            "src/openapi/petstore_spec.yaml": PETSTORE_SAMPLE_SPEC,
        }
    )

    def assert_gen(address: Address, expected: Iterable[str]) -> None:
        _assert_generated_files(
            rule_runner, address, source_roots=["src/openapi"], expected_files=expected
        )

    tgt_address = Address("src/openapi", target_name="petstore")
    assert_gen(
        tgt_address,
        [
            "src/openapi/petstore_spec.py",
        ],
    )


def test_generate_python_sources_using_custom_source_root(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "src/openapi/BUILD": "openapi_document(name='petstore', source='petstore_spec.yaml', python_source_root='src/python')",
            "src/openapi/petstore_spec.yaml": PETSTORE_SAMPLE_SPEC,
        }
    )

    def assert_gen(address: Address, expected: Iterable[str]) -> None:
        _assert_generated_files(
            rule_runner,
            address,
            source_roots=["src/openapi", "src/python"],
            expected_files=expected,
        )

    assert_gen(
        Address("src/openapi", target_name="petstore"),
        ["src/python/petstore_spec.py"],
    )


def test_python_dependency_inference(rule_runner: RuleRunner) -> None:
    rule_runner.write_files(
        {
            "src/openapi/BUILD": dedent(
                """\
                openapi_document(
                    name="petstore",
                    source="petstore_spec.yaml",
                    python_source_root='src/python',
                )
                openapi_source(source="petstore_spec.yaml")
                """
            ),
            "src/openapi/petstore_spec.yaml": PETSTORE_SAMPLE_SPEC,
            "src/python/BUILD": "python_sources()",
            "src/python/example.py": dedent(
                """\
                from petstore_spec import Pet;

                class Example:
                    Pet pet
                """
            ),
        }
    )

    source_roots = ["src/openapi", "src/python"]
    rule_runner.set_options([f"--source-root-patterns={repr(source_roots)}"])
    tgt = rule_runner.get_target(Address("src/python", relative_file_path="example.py"))
    dependencies = rule_runner.request(Addresses, [DependenciesRequest(tgt[Dependencies])])
    assert Address("src/openapi", target_name="petstore") in dependencies
