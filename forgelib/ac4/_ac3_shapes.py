"""forgelib/ac4/_ac3_shapes.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import collections
import os
import pickle
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
sys.path.insert(0, os.path.join(common.ROOT, 'maps'))
import world
BULK = {'Mesh', 'TextureMap', 'MaterialTemplate', 'Animation', 'MeshShape', 'GridCellDataBlock', 'NavMeshManager', 'RealTreeMesh', 'FakeEntities', 'WorldDataLayerManager', 'GridPartition', 'PropertyControllerData'}
MAPS = ['DataPC_AC2MP_Whiteroom.forge', 'DataPC_AC3MP_Plantation.forge', 'DataPC_AC3MP_Boston_Mill.forge', 'DataPC_AC3MP_Boston_Brewer.forge', 'DataPC_AC3MP_Boston_Harbou.forge', 'DataPC_AC3MP_Fort_Wolcott.forge', 'DataPC_AC3MP_Iceberg.forge', 'DataPC_AC3MP_Animus.forge', 'DataPC_AC3MP_Charlestown_dlc.forge', 'DataPC_AC3MP_FortMathieu_dlc.forge', 'DataPC_AC3MP_Martinique_dlc.forge']

def main():
    ref = collections.defaultdict(collections.Counter)
    owners = collections.defaultdict(collections.Counter)
    for arc in MAPS:
        try:
            path = common.ac3_archive(arc)
        except FileNotFoundError as e:
            print('skip', e)
            continue
        n = 0
        for rec, d in common.payloads(path):
            for o in common.objects_in(d):
                c = common.cls(o['type'], 'AC3')
                if c in BULK:
                    continue
                n += 1
                for name, s in world.segments(o['body']):
                    ref[name][len(s)] += 1
                    owners[name][c] += 1
        print(arc, n, 'objects', flush=True)
    pickle.dump({k: dict(v) for k, v in ref.items()}, open(os.path.join(common.OUT, '_ac3_shapes.pkl'), 'wb'))
    lines = [f'{k:40} {sum(v.values()):7} elements, {len(v):4} lengths, in {dict(owners[k].most_common(3))}' for k, v in sorted(ref.items(), key=lambda kv: -sum(kv[1].values()))]
    common.report('_ac3_shapes.txt', lines)
if __name__ == '__main__':
    main()
