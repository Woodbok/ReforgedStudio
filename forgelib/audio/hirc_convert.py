"""forgelib/audio/hirc_convert.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import collections
import io
import logging
import os
import pickle
import struct
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'tools', 'wwiser-master'))
logging.disable(logging.CRITICAL)
from wwiser.parser import wdefs, wio, wmodel, wparser
from wwiser.parser import wparser_cls as wcls
SRC_VERSION, DST_VERSION = (53, 65)

class T:
    __slots__ = ('name', 'items')

    def __init__(self, name, items=None):
        self.name, self.items = (name, items if items is not None else [])

    def _find(self, name, kind=None):
        for it in self.items:
            if it[1] == name and (kind is None or it[0] == kind):
                return it
        return None

    def has(self, name):
        return self._find(name) is not None

    def f(self, name, default=KeyError):
        it = self._find(name, 'f')
        if it is None:
            if default is KeyError:
                raise KeyError(f'{self.name}.{name}')
            return default
        return it[2]

    def o(self, name):
        it = self._find(name, 'o')
        return it[2] if it else None

    def l(self, name):
        it = self._find(name, 'l')
        return it[2] if it else None

    def pop(self, name):
        it = self._find(name)
        if it is None:
            raise KeyError(f'{self.name}.{name}')
        self.items.remove(it)
        return it[2]

    def set(self, name, value, kind='f', after=None, before=None):
        it = self._find(name)
        if it is not None:
            it[2] = value
            return
        new = [kind, name, value]
        if after is not None:
            self.items.insert(self.items.index(self._find(after)) + 1, new)
        elif before is not None:
            self.items.insert(self.items.index(self._find(before)), new)
        else:
            self.items.append(new)

    def walk(self):
        yield self
        for kind, _n, v in self.items:
            if kind == 'o':
                yield from v.walk()
            elif kind == 'l':
                for e in v:
                    yield from e.walk()

    def dump(self, depth=0):
        out = []
        for kind, n, v in self.items:
            if kind == 'f':
                out.append('  ' * depth + f'{n} = {v!r}')
            elif kind == 'o':
                out.append('  ' * depth + f'[{n}]')
                out.extend(v.dump(depth + 1))
            else:
                out.append('  ' * depth + f'[{n}] x{len(v)}')
                for i, e in enumerate(v):
                    out.append('  ' * (depth + 1) + f'#{i} {e.name}')
                    out.extend(e.dump(depth + 2))
        return out
T.__module__ = 'hirc_convert'

def to_tree(node, name=None, data=None):
    t = T(name or node.get_attrs().get('name'))
    for c in node.get_children() or []:
        kind = c.get_nodename()
        a = c.get_attrs()
        if kind == 'field':
            v = a['value']
            if a.get('type') == wmodel.TYPE_GAP and data is not None:
                off = c.get_attr('offset')
                v = bytes(data[off:off + v])
            t.items.append(['f', a['name'], v])
        elif kind == 'object':
            t.items.append(['o', a['name'], to_tree(c, data=data)])
        elif kind == 'list':
            t.items.append(['l', a['name'], [to_tree(e, data=data) for e in c.get_children() or []]])
    return t

def parse_bank(data):
    f = io.BytesIO(data)
    f.name = 'mem.bnk'
    r = wio.FileReader(f)
    r.guess_endian32(4)
    root = wmodel.NodeRoot(r)
    version = wparser.Parser()._check_header(r, root)
    while not r.is_eof():
        wparser.parse_chunk(root.node('chunk'))
    items = []
    for chunk in root.get_children():
        for c in chunk.get_children() or []:
            if c.get_nodename() == 'list' and c.get_attrs().get('name') == 'listLoadedItem':
                for e in c.get_children() or []:
                    bad = [x for x in e.get_children() or [] if x.get_nodename() in ('skip', 'error')]
                    t = to_tree(e, data=data)
                    oid = t.f('ulID', None)
                    if oid is None:
                        oid = t.f('ulStateID', None)
                    items.append((t.f('eHircType'), oid, t, bool(bad)))
    return (version, items)

class Missing(Exception):
    pass

class _WReader:
    be = False

    def __init__(self, buf):
        self.buf = buf

    def current(self):
        return len(self.buf)

    def get_filename(self):
        return 'writer'

    def get_path(self):
        return ''

class _State:

    def __init__(self):
        self.buf = bytearray()
        self.src = {}
        self.used = collections.defaultdict(int)
        self.taken = set()

    def take(self, t, name, kind):
        k = self.used[id(t), name]
        seen = 0
        for it in t.items:
            if it[1] == name and it[0] == kind:
                if seen == k:
                    self.used[id(t), name] += 1
                    self.taken.add(id(it))
                    return it[2]
                seen += 1
        raise Missing(f'{t.name}.{name} ({kind})')
_STATE = None
_ENC = {wmodel.TYPE_U8: '<B', wmodel.TYPE_S8: '<b', wmodel.TYPE_U16: '<H', wmodel.TYPE_S16: '<h', wmodel.TYPE_U32: '<I', wmodel.TYPE_SID: '<I', wmodel.TYPE_TID: '<I', wmodel.TYPE_S32: '<i', wmodel.TYPE_F32: '<f', wmodel.TYPE_D64: '<d', wmodel.TYPE_U64: '<Q', wmodel.TYPE_S64: '<q'}

def _encode(type_, value, size):
    if type_ in (wmodel.TYPE_U32, wmodel.TYPE_SID, wmodel.TYPE_TID) and value == -1:
        value = 4294967295
    if type_ in _ENC:
        return struct.pack(_ENC[type_], value)
    if type_ == wmodel.TYPE_UNI:
        return struct.pack('<f', value) if isinstance(value, float) else struct.pack('<I', value & 4294967295)
    if type_ == wmodel.TYPE_4CC:
        return value if isinstance(value, bytes) else value.encode('latin-1')
    if type_ == wmodel.TYPE_GAP:
        return value if isinstance(value, (bytes, bytearray)) else bytes(size)
    if type_ == wmodel.TYPE_STR:
        return value.encode('latin-1')
    if type_ == wmodel.TYPE_STZ:
        return value.encode('utf-8') + b'\x00'
    if type_ == wmodel.TYPE_VAR:
        out = [value & 127]
        value >>= 7
        while value:
            out.append(128 | value & 127)
            value >>= 7
        return bytes(reversed(out))
    raise ValueError(f'cannot write field type {type_}')

def _w_field(self, type_, name, value=None, size=None):
    st = _STATE
    src = st.src[id(self)]
    value = st.take(src, name, 'f')
    offset = len(st.buf)
    st.buf += _encode(type_, value, size)
    self.lastval = value
    child = wmodel.NodeField(self, offset, type_, name, value)
    self.append(child)
    return child

def _w_node(self, name):
    st = _STATE
    child = wmodel.NodeObject(self, _WReader(st.buf), name)
    self.append(child)
    st.src[id(child)] = st.take(st.src[id(self)], name, 'o')
    return child

class _WList:

    def __init__(self, elems):
        self.elems = elems

    def __iter__(self):
        return iter(self.elems)

    def preload(self):
        return self.elems

def _w_list(self, name, subname, count):
    st = _STATE
    lst = wmodel.NodeList(self, name)
    self.append(lst)
    src = st.take(st.src[id(self)], name, 'l')
    if len(src) != count:
        raise Missing(f'{st.src[id(self)].name}.{name}: count {count} but tree has {len(src)}')
    elems = []
    for i, s in enumerate(src):
        e = wmodel.NodeObject(lst, _WReader(st.buf), subname or name)
        e._index = i
        lst.append(e)
        st.src[id(e)] = s
        elems.append(e)
    return _WList(elems)

def _w_peek32(self):
    raise Missing('peek32 (look-ahead read) is not supported by the writer')

def write_item(hirc_type, t):
    global _STATE
    st = _State()
    _STATE = st
    saved = (wmodel.NodeObject.field, wmodel.NodeObject.node, wmodel.NodeObject.list, wmodel.NodeObject.peek32)
    wmodel.NodeObject.field, wmodel.NodeObject.node = (_w_field, _w_node)
    wmodel.NodeObject.list, wmodel.NodeObject.peek32 = (_w_list, _w_peek32)
    try:
        root = wmodel.NodeRoot(_WReader(st.buf))
        root.set_version(DST_VERSION)
        root.set_feedback(True)
        elem = root.node('item')
        st.src[id(elem)] = t
        dispatch = wparser.get_hirc_dispatch(elem).get(hirc_type)
        if dispatch is None:
            raise Missing(f'no v{DST_VERSION} parser for HIRC type {hirc_type}')
        dispatch(elem)
    finally:
        wmodel.NodeObject.field, wmodel.NodeObject.node, wmodel.NodeObject.list, wmodel.NodeObject.peek32 = saved
        _STATE = None
    dropped = []
    for node in t.walk():
        for it in node.items:
            if it[0] == 'f' and id(it) not in st.taken and (it[1] not in ('eHircType', 'dwSectionSize')):
                dropped.append(f'{node.name}.{it[1]}')
    return (bytes(st.buf), dropped)

def set_version(v):
    wdefs.setup(v)
    wcls.setup()
CACHE = os.path.join(HERE, 'cache')

def _parsed(game, root):
    path = os.path.join(CACHE, f'hirc_trees_{game}.pkl')
    if os.path.exists(path):
        return pickle.load(open(path, 'rb'))
    from banks import load
    pkgs, banks = load(root)
    pk = {p.name: p for p in pkgs}
    out, bad = ({}, 0)
    for b in banks:
        try:
            _v, items = parse_bank(pk[b.pkg].read(b.entry))
        except Exception:
            bad += 1
            continue
        for t_, oid, t, skipped in items:
            if not skipped:
                out.setdefault((t_, oid), t)
    pickle.dump(out, open(path, 'wb'))
    print(f'parsed {game}: {len(out)} objects ({bad} banks failed to parse)')
    return out

def tree_diff(a, b, path=''):
    out = []
    if b is None:
        return ['AC3 tree missing']
    skip = ('eHircType', 'dwSectionSize')

    def groups(t):
        g = collections.OrderedDict()
        for k, n, v in t.items:
            if n not in skip:
                g.setdefault((k, n), []).append(v)
        return g
    ga, gb = (groups(a), groups(b))
    if set(ga) != set(gb):
        out.append(f'{path}{a.name}: fields {sorted((n for _, n in ga))} vs {sorted((n for _, n in gb))}')
        return out
    for key, va in ga.items():
        k, n = key
        vb = gb[key]
        if len(va) != len(vb):
            out.append(f'{path}{a.name}.{n}: {len(va)} occurrences vs {len(vb)}')
            continue
        for x, y in zip(va, vb):
            if k == 'f' and x != y:
                out.append(f'{path}{a.name}.{n}: {x!r} vs {y!r}')
            elif k == 'o':
                out += tree_diff(x, y, f'{path}{a.name}.')
            elif k == 'l':
                if len(x) != len(y):
                    brief = lambda l: [tuple((v for _k, _n, v in e.items if _k == 'f')) for e in l]
                    out.append(f'{path}{a.name}.{n}: {brief(x)} vs {brief(y)}')
                else:
                    for i, (e1, e2) in enumerate(zip(x, y)):
                        out += tree_diff(e1, e2, f'{path}{a.name}.{n}[{i}].')
    return out
CONTENT_NOISE = ('uInMemoryMediaSize', 'uFileOffset', 'uFileID', 'sourceID', 'DirectParentID', 'Children', 'ulChildID', 'ulNumChilds', 'ulPlayID', 'pItems', 'ulPlayListItem', 'AkPropBundle<AkPropValue,unsigned char>', 'AkPropBundle<RANGED_MODIFIERS', 'OverrideBusId', 'PlayActionParams.fileID', 'ulTargetID', 'fDuration', 'fPosition', 'fPlayAt', 'fSrcDuration', 'fBeginTrimOffset', 'fEndTrimOffset', 'numClipAutomationItem', 'pAssocs', 'numSwitchAssocs', 'SwitchList', 'ulNumSwitchGroups', 'rParams', 'ulNumSwitchParams')

def score(types=None, show=0):
    from akpk import ACR_SOUND, AC3_SOUND
    import rules
    acr_raw, ac3_raw = pickle.load(open(os.path.join(CACHE, 'hirc_pairs.pkl'), 'rb'))
    set_version(SRC_VERSION)
    acr = _parsed('acr', ACR_SOUND)
    set_version(DST_VERSION)
    ac3 = _parsed('ac3', AC3_SOUND)
    set_version(DST_VERSION)
    names = {v: k for k, v in wparser.get_hirc_dispatch(wmodel.NodeRoot(_WReader(bytearray()))).items()}
    res = collections.defaultdict(collections.Counter)
    examples = collections.defaultdict(list)
    paths = collections.defaultdict(collections.Counter)
    for (t_, oid), a in acr.items():
        if (t_, oid) not in ac3_raw or (types and str(t_) not in types):
            continue
        r = res[t_]
        r['pairs'] += 1
        try:
            tree = rules.convert(t_, pickle.loads(pickle.dumps(a)))
            body, dropped = write_item(t_, tree)
        except Missing as e:
            r[f'missing {e}'] += 1
            continue
        except Exception as e:
            r[f'error {type(e).__name__}: {e}'[:120]] += 1
            continue
        for d in dropped:
            r[f'dropped {d}'] += 1
        if body == ac3_raw[t_, oid]:
            r['exact'] += 1
        else:
            r['differs'] += 1
            import re as _re
            diffs = [_re.sub('\\[\\d+\\]', '[]', d.split(':')[0]) for d in tree_diff(tree, ac3.get((t_, oid)))]
            real = [d for d in diffs if not any((n in d for n in CONTENT_NOISE))]
            if not real:
                r['structural'] += 1
            for d in real:
                paths[t_][d] += 1
            if len(examples[t_]) < show:
                examples[t_].append((oid, tree, ac3.get((t_, oid))))
    if show:
        for t_, exs in examples.items():
            for oid, mine, theirs in exs:
                print(f'  -- type {t_} {oid:#010x}: ' + '; '.join(tree_diff(mine, theirs)[:6]))
    for t_, r in sorted(res.items(), key=lambda kv: -kv[1]['pairs']):
        ok = r['exact'] + r['structural']
        head = f"type {t_:2} pairs {r['pairs']:6}  exact {r['exact']:6} ({100 * r['exact'] // max(1, r['pairs'])}%)  +structural {r['structural']:5} = {100 * ok // max(1, r['pairs'])}%  other diffs {r['differs'] - r['structural']}"
        print(head)
        for k, v in sorted(r.items(), key=lambda kv: -kv[1]):
            if k not in ('pairs', 'exact', 'differs', 'structural'):
                print(f'     {v:6}  {k}')
        for pth, v in paths[t_].most_common(8):
            print(f'     {v:6}  diff at {pth}')
    return (res, examples)
if __name__ == '__main__':
    import hirc_convert
    if sys.argv[1:2] == ['--score']:
        hirc_convert.score(sys.argv[2:] or None)
