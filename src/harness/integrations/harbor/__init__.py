"""Harbor as a second execution backend.

Harbor (https://github.com/harbor-framework/harbor) runs in its own virtual environment and is
only ever called as a separate process through its CLI. Nothing here imports it; the base
install can import Harbor results without Harbor, Docker or keys.

Only one release is supported. Its job layout and result schema were checked against real
output of that release (see ``tests/fixtures/harbor/``); other versions are refused, not
parsed on a guess.
"""

PINNED_VERSION = "0.25.0"
# Upstream wheel, as published on PyPI.
WHEEL_SHA256 = "51923ac567fc57b03279e93468b105f689628bcac2cd3d541d4e44aa5c0ec309"
# ``lock.json`` of a job written by the pinned release.
JOB_LOCK_SCHEMA = 3
LOCK_FILE = "configs/harbor/requirements-0.25.0.lock"
