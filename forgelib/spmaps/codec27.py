"""codec27: AC3 singleplayer's FastLoad bodies (8-byte ids) - the Studio's ac3editor/spcodec.py, which decodes with
ac3codec and the schemas read from the game's exes. ac3editor.mapdata registers it under this name; this file covers a
module imported before that."""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
from ac3editor.spcodec import *  # noqa: E402,F401,F403
from ac3editor.spcodec import (MP_PREFIX, Codec64, DecodeError, PartialCodec64, Property,  # noqa: E402,F401
                               geometry_schema, mp_schema, sp_schema)
