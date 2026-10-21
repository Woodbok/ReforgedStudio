"""forgelib/crossmaps/acb_trees.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
AC2_TEX1_PIXELLIT = 1921337557
_done = []

def pixel_lit(body):
    return len(body) == 209 and body[10] == 1 and (body[11] in (0, 1)) and (struct.unpack_from('<I', body, 12)[0] == AC2_TEX1_PIXELLIT)

def patch():
    if _done:
        return
    import realtree_convert
    orig = realtree_convert.acr_colour

    def acr_colour(source, objs, cls):
        b = source['body']
        if pixel_lit(b):
            v = struct.unpack_from('<4f', b, len(b) - 16)
            if not all((0.0 < x <= 4.0 for x in v)):
                raise ValueError(f"{source['name']}: pixel-lit colour {v} out of range")
            return v
        return orig(source, objs, cls)
    realtree_convert.acr_colour = acr_colour
    _done.append(True)
