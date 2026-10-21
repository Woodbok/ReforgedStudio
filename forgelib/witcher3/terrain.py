"""forgelib/witcher3/terrain.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import os
import sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from witcher3 import bundles, cr2w
WORLDS = {'kaer_morhen': 'levels\\kaer_morhen\\kaer_morhen.w2w', 'novigrad': 'levels\\novigrad\\novigrad.w2w', 'skellige': 'levels\\skellige\\skellige.w2w', 'prolog_village': 'levels\\prolog_village\\prolog_village.w2w', 'prolog_village_winter': 'levels\\prolog_village_winter\\prolog_village.w2w', 'wyzima_castle': 'levels\\wyzima_castle\\wyzima_castle.w2w', 'island_of_mist': 'levels\\island_of_mist\\island_of_mist.w2w', 'the_spiral': 'levels\\the_spiral\\spiral.w2w', 'bob': 'dlc\\bob\\data\\levels\\bob\\bob.w2w'}
HEIGHT_BUFFER = {0: 1, 1: 3, 2: 5, 3: 8}
CONTROL_BUFFER = {0: 2, 1: 4, 2: 6, 3: 9}

class Terrain:

    def __init__(self, world):
        self.world = world
        self.path = WORLDS.get(world, world)
        f = cr2w.load(self.path)
        cm = f.by_class('CClipMap')
        if not cm:
            raise ValueError('%s: no CClipMap' % self.path)
        p = cm[0].props
        self.size = p['terrainSize']
        c = p['terrainCorner']
        self.corner = (c['X'], c['Y'])
        self.tiles = p['numTilesPerEdge']
        self.res = p['tileRes']
        self.low = p['lowestElevation']
        self.high = p['highestElevation']
        self.tile_size = self.size / self.tiles
        self.dir = os.path.dirname(self.path) + '\\terrain_tiles\\'
        self._cache = {}

    def tile(self, row, col, mip=0):
        k = (row, col, mip)
        if k not in self._cache:
            n = self.dir + 'tile_%d_x_%d_res%d.w2ter.%d.buffer' % (row, col, self.res, HEIGHT_BUFFER[mip])
            if not bundles.exists(n):
                self._cache[k] = None
            else:
                r = self.res >> mip
                v = np.frombuffer(bundles.read(n), '<u2').reshape(r, r).astype(np.float32)
                self._cache[k] = self.low + v / 65535.0 * (self.high - self.low)
        return self._cache[k]

    def control_tile(self, row, col, mip=0):
        k = ('c', row, col, mip)
        if k not in self._cache:
            n = self.dir + 'tile_%d_x_%d_res%d.w2ter.%d.buffer' % (row, col, self.res, CONTROL_BUFFER[mip])
            r = self.res >> mip
            self._cache[k] = np.frombuffer(bundles.read(n), '<u2').reshape(r, r).copy() if bundles.exists(n) else None
        return self._cache[k]

    def region(self, box, mip=0, control=False):
        step = self.tile_size / (self.res >> mip)
        cx, cy = self.corner
        i0 = int(np.floor((box[0] - cx) / step))
        i1 = int(np.ceil((box[2] - cx) / step))
        j0 = int(np.floor((box[1] - cy) / step))
        j1 = int(np.ceil((box[3] - cy) / step))
        r = self.res >> mip
        out = np.zeros((j1 - j0 + 1, i1 - i0 + 1), np.uint16) if control else np.full((j1 - j0 + 1, i1 - i0 + 1), np.nan, np.float32)
        for row in range(max(0, j0 // r), min(self.tiles, j1 // r + 1)):
            for col in range(max(0, i0 // r), min(self.tiles, i1 // r + 1)):
                t = self.control_tile(row, col, mip) if control else self.tile(row, col, mip)
                if t is None:
                    continue
                a0, a1 = (max(j0, row * r), min(j1 + 1, (row + 1) * r))
                b0, b1 = (max(i0, col * r), min(i1 + 1, (col + 1) * r))
                out[a0 - j0:a1 - j0, b0 - i0:b1 - i0] = t[a0 - row * r:a1 - row * r, b0 - col * r:b1 - col * r]
        return (out, cx + i0 * step, cy + j0 * step, step)

    def height(self, x, y, mip=0):
        x = np.asarray(x, np.float64)
        y = np.asarray(y, np.float64)
        pad = 2 * self.tile_size / (self.res >> mip)
        h, x0, y0, s = self.region((x.min() - pad, y.min() - pad, x.max() + pad, y.max() + pad), mip)
        fx = (x - x0) / s
        fy = (y - y0) / s
        i = np.clip(np.floor(fx).astype(int), 0, h.shape[1] - 2)
        j = np.clip(np.floor(fy).astype(int), 0, h.shape[0] - 2)
        u = fx - i
        v = fy - j
        return (h[j, i] * (1 - u) + h[j, i + 1] * u) * (1 - v) + (h[j + 1, i] * (1 - u) + h[j + 1, i + 1] * u) * v

    def mesh(self, box, mip=0, control=False):
        h, x0, y0, s = self.region(box, mip)
        ny, nx = h.shape
        xs = x0 + np.arange(nx) * s
        ys = y0 + np.arange(ny) * s
        X, Y = np.meshgrid(xs, ys)
        V = np.stack([X.ravel(), Y.ravel(), h.ravel()], 1)
        idx = np.arange(ny * nx).reshape(ny, nx)
        a, b, c, d = (idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel())
        T = np.concatenate([np.stack([a, b, d], 1), np.stack([a, d, c], 1)])
        ok = ~np.isnan(V[T, 2]).any(1)
        if control:
            c = self.region(box, mip, control=True)[0].ravel()
            return (V, T[ok], c[T[ok][:, 0]])
        return (V, T[ok])

def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('world')
    ap.add_argument('--box', type=float, nargs=4)
    ap.add_argument('--mip', type=int, default=0)
    ap.add_argument('--png')
    a = ap.parse_args(argv)
    t = Terrain(a.world)
    print('%s: %.0f m, corner %s, %d x %d tiles of %.0f m, res %d, z %.0f..%.0f' % (t.path, t.size, t.corner, t.tiles, t.tiles, t.tile_size, t.res, t.low, t.high))
    if a.box:
        h, x0, y0, s = t.region(a.box, a.mip)
        print('region %s: %s samples, step %.3f, z %.1f..%.1f' % (a.box, h.shape, s, np.nanmin(h), np.nanmax(h)))
        if a.png:
            from PIL import Image
            g = (h - np.nanmin(h)) / max(0.001, np.nanmax(h) - np.nanmin(h)) * 255
            Image.fromarray(np.nan_to_num(g[::-1]).astype(np.uint8)).save(a.png)
if __name__ == '__main__':
    main(sys.argv[1:])
