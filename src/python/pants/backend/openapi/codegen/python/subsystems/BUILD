# Copyright 2024 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).
# Copyright 2024 Pants project contributors (see CONTRIBUTORS.md).

# Licensed under the Apache License, Version 2.0 (see LICENSE).
resource(name="datamodel_code_generator_lockfile", source="datamodel_code_generator.lock")
python_sources(
    overrides={
        "datamodel_code_generator.py": {"dependencies": [":datamodel_code_generator_lockfile"]}
    },
)
