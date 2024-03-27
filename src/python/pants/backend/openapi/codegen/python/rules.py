# Copyright 2024 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).
import logging
import pathlib
from dataclasses import dataclass
from pathlib import PurePath

from pants.backend.openapi.codegen.python.extra_fields import PythonSkipField, PythonSourceRootField
from pants.backend.openapi.codegen.python.subsystems.datamodel_code_generator import (
    DatamodelCodeGenerator,
)
from pants.backend.openapi.target_types import OpenApiDocumentField, OpenApiSourceField
from pants.backend.python.target_types import PythonSourceField
from pants.backend.python.util_rules import pex
from pants.backend.python.util_rules.pex import PexRequest, VenvPex, VenvPexProcess
from pants.core.util_rules.source_files import SourceFilesRequest
from pants.core.util_rules.stripped_source_files import StrippedSourceFiles
from pants.engine.fs import AddPrefix, CreateDigest, Digest, Directory, MergeDigests, Snapshot
from pants.engine.internals.native_engine import EMPTY_SNAPSHOT, RemovePrefix
from pants.engine.process import ProcessResult
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    FieldSet,
    GeneratedSources,
    GenerateSourcesRequest,
    TransitiveTargets,
    TransitiveTargetsRequest,
)
from pants.engine.unions import UnionRule
from pants.source.source_root import SourceRoot, SourceRootRequest
from pants.util.logging import LogLevel
from pants.util.strutil import pluralize

logger = logging.getLogger(__name__)


class GeneratePythonFromOpenApiRequest(GenerateSourcesRequest):
    input = OpenApiDocumentField
    output = PythonSourceField


@dataclass(frozen=True)
class OpenApiDocumentDatamodelCodeGeneratorFieldSet(FieldSet):
    required_fields = (OpenApiDocumentField,)

    source: OpenApiDocumentField
    python_source_root: PythonSourceRootField
    skip: PythonSkipField


@rule(desc="Generate Python from OpenAPI document", level=LogLevel.DEBUG)
async def generate_python_from_openapi(
    request: GeneratePythonFromOpenApiRequest,
    datamodel_code_generator: DatamodelCodeGenerator,
) -> GeneratedSources:
    field_set = OpenApiDocumentDatamodelCodeGeneratorFieldSet.create(request.protocol_target)

    if field_set.skip.value:
        return GeneratedSources(EMPTY_SNAPSHOT)

    output_dir = "_generated_files"

    transitive_targets = await Get(TransitiveTargets, TransitiveTargetsRequest([field_set.address]))
    target_stripped_sources_request = Get(
        StrippedSourceFiles, SourceFilesRequest([field_set.source])
    )
    all_stripped_sources_request = Get(
        StrippedSourceFiles,
        SourceFilesRequest(
            tgt[OpenApiSourceField]
            for tgt in transitive_targets.closure
            if tgt.has_field(OpenApiSourceField)
        ),
    )
    datamodel_code_generator_pex_request = Get(
        VenvPex, PexRequest, datamodel_code_generator.to_pex_request()
    )
    target_stripped_sources, all_stripped_sources, datamodel_code_generator_pex = await MultiGet(
        target_stripped_sources_request,
        all_stripped_sources_request,
        datamodel_code_generator_pex_request,
    )

    input_file = pathlib.Path(target_stripped_sources.snapshot.files[0])
    models_output_dir = pathlib.Path(output_dir, input_file.parent)
    models_output_dir_digest = await Get(Digest, CreateDigest([Directory(str(models_output_dir))]))
    input_digest = await Get(
        Digest,
        MergeDigests(
            [
                models_output_dir_digest,
                target_stripped_sources.snapshot.digest,
                all_stripped_sources.snapshot.digest,
            ]
        ),
    )

    result = await Get(
        ProcessResult,
        VenvPexProcess(
            datamodel_code_generator_pex,
            argv=(
                "--input",
                str(input_file),
                "--input-file-type",
                "openapi",
                "--output",
                str(models_output_dir.joinpath(input_file.with_suffix(".py").name)),
            ),
            input_digest=input_digest,
            output_directories=(output_dir,),
            description=f"Run datamodel-code-generator on {pluralize(len(target_stripped_sources.snapshot.files), 'file')}.",
            level=LogLevel.DEBUG,
        ),
    )

    normalized_digest, source_root = await MultiGet(
        Get(Digest, RemovePrefix(result.output_digest, output_dir)),
        Get(
            SourceRoot,
            SourceRootRequest,
            SourceRootRequest(PurePath(field_set.python_source_root.value))
            if field_set.python_source_root.value
            else SourceRootRequest.for_target(request.protocol_target),
        ),
    )

    source_root_restored = (
        await Get(Snapshot, AddPrefix(normalized_digest, source_root.path))
        if source_root.path != "."
        else await Get(Snapshot, Digest, normalized_digest)
    )

    return GeneratedSources(source_root_restored)


def rules():
    return [
        *collect_rules(),
        *pex.rules(),
        UnionRule(GenerateSourcesRequest, GeneratePythonFromOpenApiRequest),
    ]
