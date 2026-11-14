# -*- coding: utf-8 -*-
'''
    feedgen.migrate
    ~~~~~~~~~~~~~~~

    Convenience re-export of the RSS guid migration helpers. This module exists
    so that both ``feedgen.migration`` and ``feedgen.migrate`` can be imported,
    depending on the naming convention of the calling project.

    :license: FreeBSD and LGPL, see license.* for more details.
'''

from feedgen.migration import (  # noqa: F401
    AppliedGuidMapping,
    ConflictGroup,
    GuidMapping,
    GuidMappingPlan,
    IdMapping,
    MappingConflict,
    MappingError,
    MappingPlan,
    MappingSource,
    RejectedGuidMapping,
    ResolvedArticle,
    main,
    migrate_guid_mappings,
    normalize_mappings,
    resolve_guid_mappings,
    resolve_mappings,
)

__all__ = [
    'AppliedGuidMapping', 'ConflictGroup', 'GuidMapping', 'GuidMappingPlan',
    'IdMapping', 'MappingConflict', 'MappingError', 'MappingPlan',
    'MappingSource', 'RejectedGuidMapping', 'ResolvedArticle', 'main',
    'migrate_guid_mappings', 'normalize_mappings', 'resolve_guid_mappings',
    'resolve_mappings',
]

if __name__ == '__main__':
    raise SystemExit(main())
