"""forgelib/spmaps/sp_assets.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import struct
import sys
import zlib
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
from codec27 import Codec64, geometry_schema
from forge27 import Forge27, Forge27Error
import sp_topview as T
H = {n: zlib.crc32(n.encode()) for n in ('Mesh', 'Material', 'TextureSet', 'TextureMap', 'LODSelector')}
UV_AT = {0: 20, 1: 20, 3: 20, 4: 16}
SHADER_PARAMS = ('Layer0_Color', 'Layer1_Color', 'Layer0_UVScale', 'Layer1_UVScale', 'PixelBlenderScale', 'PixelBlenderBias')
SHADER_PARAM_OF = {zlib.crc32(n.encode()): n for n in SHADER_PARAMS}

def ptr_id(v):
    b = getattr(v, 'id', None) or getattr(v, 'link', None)
    if b is None and getattr(v, 'obj', None) is not None:
        b = v.obj.id
    return struct.unpack('<Q', b)[0] if b else None
SHARED = ('DataPC_DX11.forge', 'DataPC_DX11_extra.forge')

def owners(archive):
    import pickle
    f = Forge27(archive)
    st = os.stat(f.path)
    path = os.path.join(T.OUT, 'cache', f'{T.stem(archive)}_owners_v1.pkl')
    key = (st.st_size, int(st.st_mtime))
    if os.path.exists(path):
        with open(path, 'rb') as fh:
            d = pickle.load(fh)
        if d['key'] == key:
            return (f, d['owner'])
    own = T.object_owners(f, log=None)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        pickle.dump({'key': key, 'owner': own}, fh)
    return (f, own)

class Assets:

    def __init__(self, archive, shared=SHARED):
        self.forges = []
        for a in (archive,) + tuple(shared):
            f, own = owners(a)
            self.forges.append((f, own))
        self.f = self.forges[0][0]
        self.owner = self.forges[0][1]
        self.pool = np.array(sorted(set().union(*(own for _f, own in self.forges))), dtype='<u8')
        self.C = Codec64(geometry_schema())
        self._objs = {}

    def obj(self, uid):
        for i, (f, own) in enumerate(self.forges):
            ru = own.get(uid)
            if ru is None:
                continue
            if (i, ru) not in self._objs:
                try:
                    self._objs[i, ru] = {o['uid']: o for o in f.objects(f.by_uid[ru])}
                except (Forge27Error, struct.error, KeyError):
                    self._objs[i, ru] = {}
            o = self._objs[i, ru].get(uid)
            if o is not None:
                return o
        return None

    def mesh_parts(self, uid, skin_scale=None):
        o = self.obj(uid)
        if o is None or o['type'] != H['Mesh']:
            return []
        root = self.C.decode(o['body']).obj
        cm = root.fields['CompiledMesh'].obj
        md = cm.fields['MeshData'].fields
        fmt, st = (md['VertexFormat'][0], md['VertexStride'][0])
        vb = b''.join(md['VertexBufferData'])
        n = len(vb) // st
        raw = np.frombuffer(vb[:n * st], dtype=np.uint8).reshape(n, st)
        q = raw[:, :8].copy().view('<i2').reshape(n, 4).astype(np.float64)
        if fmt in (0, 1):
            if skin_scale is None:
                return []
            verts = q[:, :3] * skin_scale
        else:
            verts = q[:, :3] * (q[:, 3:4] / 32767.0)
        at = UV_AT.get(fmt)
        uv = raw[:, at:at + 4].copy().view('<i2').reshape(n, 2).astype(np.float64) / 2048.0 if at and at + 4 <= st else np.zeros((n, 2))
        color = raw[:, 16:20].copy() if fmt == 3 and st >= 20 else None
        blend = raw[:, 11].copy() if fmt in (3, 4) and st >= 12 else None
        normals = (raw[:, 8:11].astype(np.float64) - 127.5) / 127.5 if fmt in (3, 4) and st >= 12 else None
        idx = np.frombuffer(b''.join(md['IndexBufferData']), dtype='<u4' if md['IsIndexBuffer32bit'][0] else '<u2')
        mats = {}
        for k, x in enumerate(cm.fields['InstancingData']):
            xo = getattr(x, 'obj', None) or x
            mats[k] = ptr_id(xo.fields['Material'])
        out = []
        for k, p in enumerate(md['StandardPrimitives']):
            g = {kk: struct.unpack('<i', v)[0] for kk, v in (getattr(p, 'obj', None) or p).fields.items() if isinstance(v, bytes) and len(v) == 4}
            if g.get('Type', 1) != 1:
                continue
            seg = idx[g['StartIndex']:g['StartIndex'] + 3 * g['PrimitiveCount']].astype(np.int64)
            base = g.get('MinIndex', 0)
            if base and len(seg) and (seg.min() < base):
                seg = seg + base
            tris = seg[:len(seg) // 3 * 3].reshape(-1, 3)
            tris = tris[(tris < n).all(axis=1)]
            used, inv = np.unique(tris, return_inverse=True)
            out.append(dict(verts=verts[used], tris=inv.reshape(-1, 3), uv=uv[used], material=mats.get(k), primitive=k, format=fmt, color=color[used] if color is not None else None, blend=blend[used] if blend is not None else None, normals=normals[used] if normals is not None else None))
        return out

    def material_texture_uid(self, mat_uid):
        o = self.obj(mat_uid)
        if o is None:
            return None
        for u in T.u64s(o['body'], self.pool):
            s = self.obj(u)
            if s is None or s['type'] != H['TextureSet']:
                continue
            maps = [ptr_id(m) for m in self.C.decode(s['body']).obj.fields['Maps']]
            maps = [m for m in maps if m and self.obj(m) is not None]
            for m in maps:
                if 'diffuse' in self.obj(m)['name'].lower():
                    return m
            return maps[0] if maps else None
        for u in T.u64s(o['body'], self.pool):
            t = self.obj(u)
            if t is not None and t['type'] == H['TextureMap'] and ('diffuse' in t['name'].lower()):
                return u
        return None

    def material_layers(self, mat_uid):
        o = self.obj(mat_uid)
        if o is None:
            return []
        out = []
        for u in T.u64s(o['body'], self.pool):
            s = self.obj(u)
            if s is None or s['type'] != H['TextureSet']:
                continue
            maps = [ptr_id(m) for m in self.C.decode(s['body']).obj.fields['Maps']]
            maps = [m for m in maps if m and self.obj(m) is not None]
            d = next((m for m in maps if 'diffuse' in self.obj(m)['name'].lower()), maps[0] if maps else None)
            if d is not None and d not in out:
                out.append(d)
        return out

    def material_params(self, mat_uid):
        o = self.obj(mat_uid)
        if o is None:
            return {}
        b = o['body']
        out = {}
        for i in range(len(b) - 16):
            n = SHADER_PARAM_OF.get(struct.unpack_from('<I', b, i)[0])
            if n is None or n in out:
                continue
            kind = struct.unpack_from('<I', b, i + 8)[0]
            k = {851968: 4, 655360: 1}.get(kind)
            if k is None or i + 16 + 4 * k > len(b):
                continue
            out[n] = struct.unpack_from(f'<{k}f', b, i + 16)
        return out

    def texture_rgba(self, tex_uid, max_size=1024):
        o = self.obj(tex_uid)
        if o is None:
            return None
        b = o['body']
        w, h, _d, fmt = struct.unpack_from('<4I', b, 14)
        mips = struct.unpack_from('<I', b, 38)[0]
        if fmt not in (2, 3, 4, 5):
            return None
        bs = 8 if fmt in (2, 3) else 16
        size = struct.unpack_from('<I', b, 159)[0]
        data = b[163:163 + size]
        off, lw, lh = (0, w, h)
        for _ in range(mips):
            if lw <= max_size and lh <= max_size:
                break
            off += max(1, lw // 4) * max(1, lh // 4) * bs
            lw, lh = (max(1, lw // 2), max(1, lh // 2))
        need = max(1, lw // 4) * max(1, lh // 4) * bs
        if off + need > len(data):
            return None
        return (o['name'], decode_dxt(data[off:off + need], lw, lh, fmt))

def decode_dxt(data, w, h, fmt):
    bw, bh = (max(1, w // 4), max(1, h // 4))
    bs = 8 if fmt in (2, 3) else 16
    blk = np.frombuffer(data, dtype=np.uint8)[:bw * bh * bs].reshape(-1, bs)
    col = blk[:, -8:]
    c0 = col[:, 0].astype(np.uint32) | col[:, 1].astype(np.uint32) << 8
    c1 = col[:, 2].astype(np.uint32) | col[:, 3].astype(np.uint32) << 8

    def rgb(c):
        return np.stack([(c >> 11 & 31) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31], 1).astype(np.int32)
    a, b_ = (rgb(c0), rgb(c1))
    four = (c0 > c1) | (fmt != 2 and fmt != 3)
    pal = np.stack([a, b_, np.where(four[:, None], (2 * a + b_) // 3, (a + b_) // 2), np.where(four[:, None], (a + 2 * b_) // 3, 0)], 1)
    bits = col[:, 4:8].astype(np.uint32)
    word = bits[:, 0] | bits[:, 1] << 8 | bits[:, 2] << 16 | bits[:, 3] << 24
    sel = word[:, None] >> 2 * np.arange(16, dtype=np.uint32) & 3
    px = np.take_along_axis(pal, sel[:, :, None].astype(np.int64).repeat(3, 2), 1)
    alpha = np.full((len(blk), 16), 255, dtype=np.int32)
    if fmt in (2, 3):
        alpha = np.where(~four[:, None] & (sel == 3), 0, 255)
    elif fmt == 4:
        ab = blk[:, :8].astype(np.uint32)
        nib = np.stack([ab[:, i // 2] >> 4 * (i % 2) & 15 for i in range(16)], 1)
        alpha = (nib * 17).astype(np.int32)
    elif fmt == 5:
        a0, a1 = (blk[:, 0].astype(np.int32), blk[:, 1].astype(np.int32))
        ab = blk[:, 2:8].astype(np.uint64)
        aw = sum((ab[:, i] << np.uint64(8 * i) for i in range(6)))
        asel = (aw[:, None] >> np.uint64(3) * np.arange(16, dtype=np.uint64) & np.uint64(7)).astype(np.int32)
        k = np.arange(8)
        eight = np.where(k[None, :] == 0, a0[:, None], np.where(k[None, :] == 1, a1[:, None], ((8 - k[None, :]) * a0[:, None] + (k[None, :] - 1) * a1[:, None]) // 7))
        six = np.where(k[None, :] == 0, a0[:, None], np.where(k[None, :] == 1, a1[:, None], np.where(k[None, :] < 6, ((6 - k[None, :]) * a0[:, None] + (k[None, :] - 1) * a1[:, None]) // 5, np.where(k[None, :] == 6, 0, 255))))
        apal = np.where((a0 > a1)[:, None], eight, six)
        alpha = np.take_along_axis(apal, asel, 1)
    rgba = np.concatenate([px, alpha[:, :, None]], 2).clip(0, 255).astype(np.uint8)
    img = rgba.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:h, :w]
if __name__ == '__main__':
    from PIL import Image
    arch, name = (sys.argv[1], sys.argv[2])
    A = Assets(arch)
    r = A.f.by_name[name]
    mesh = next((o for o in A.f.objects(r) if o['type'] == H['Mesh']))
    out = os.path.join(T.OUT, 'assets')
    os.makedirs(out, exist_ok=True)
    for p in A.mesh_parts(mesh['uid']):
        tu = A.material_texture_uid(p['material']) if p['material'] else None
        tx = A.texture_rgba(tu) if tu else None
        print(f"part {p['primitive']}: {len(p['verts'])} verts, {len(p['tris'])} tris, uv {p['uv'].min(0).round(2)}..{p['uv'].max(0).round(2)}, material {(A.obj(p['material'])['name'] if p['material'] and A.obj(p['material']) else None)}, texture {(tx[0] if tx else None)} {(tx[1].shape if tx else '')}")
        if tx:
            Image.fromarray(tx[1]).save(os.path.join(out, f'{tx[0]}.png'))
