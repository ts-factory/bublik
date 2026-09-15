# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import re

from bublik.core.config.services import ConfigServices
from bublik.data.models import GlobalConfigs


REF_CORE = r'ref://([^/\s]+)/([\w\-/:]+)'
REF_PATTERN = re.compile(REF_CORE)


def resolve_ref(ref, project_id=None):
    """
    Parse a single 'ref://TRACKER/KEY' reference and resolve its URL
    against the project's configured issue trackers (REFERENCES.ISSUES).
    If project_id is None, the default (project-less) config is used.

    Args:
        ref: A single reference string, e.g. 'ref://JIRA/FOO-123'
        project_id: The project whose config to use, or None for the default

    Returns:
        (ref_type, ref_tail, url) tuple, or None if ref doesn't match the
        ref://TRACKER/KEY shape. url is None if the tracker isn't
        configured (for the given project or the default).
    """
    match = REF_PATTERN.fullmatch(ref)
    if not match:
        return None
    ref_type, ref_tail = match.group(1), match.group(2)

    logs = ConfigServices.getattr_from_global(GlobalConfigs.REFERENCES.name, 'ISSUES', project_id)
    url = f'{logs[ref_type]["uri"]}{ref_tail}' if ref_type in logs else None

    return ref_type, ref_tail, url
