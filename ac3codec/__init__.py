"""Reforged Studio's FastLoad object codec: AC3's object bodies <-> trees, against a neutral schema model.

    from ac3codec import Codec, load_json
    C = Codec(load_json("AC3_MP.schema.json"))
    root = C.decode(body)          # Root(pre_header, status, obj); obj.fields is an ordered dict
    assert C.encode(root) == body

README.md describes the schema model, the format and the API.
"""
from .codec import Codec, DecodeError, EncodeError, Handle, Obj, Ptr, Ref, Root
from .schema import Schema, from_anvilforge, load_json, save_json

__all__ = ["Codec", "DecodeError", "EncodeError", "Handle", "Obj", "Ptr", "Ref", "Root",
           "Schema", "from_anvilforge", "load_json", "save_json"]
