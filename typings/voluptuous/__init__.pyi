# Home Assistant 2026.10 and later replace voluptuous with probatio, an
# API-compatible successor, when it is imported. The integration keeps importing
# voluptuous so it also runs on older releases, and is type checked against what
# that import yields on current ones.
from probatio import *  # noqa: F403
