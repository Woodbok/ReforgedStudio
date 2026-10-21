"""forgelib/tables.py: part of forgelib, the AC3 archive and map library Reforged Studio runs on."""
import struct
BUILDROW = 881535190
CELL_MARK = b'\x00\x00\x12\x00\x00'
CELL_SIZE = 17
VALUE_SIZES = {10: 16, 13: 28, 14: 28}

def cell_size(body, start):
    return CELL_SIZE if body[start + 8:start + 13] == CELL_MARK else VALUE_SIZES[body[start + 10]]

def rows_of(body, values=True):
    out, i = ([], 0)
    while i + 8 <= len(body):
        if struct.unpack_from('<I', body, i + 4)[0] != BUILDROW:
            i += 1
            continue
        count_at = i + 4 + 4 + 1 + 4 + 4 + 4
        n = struct.unpack_from('<I', body, count_at)[0]
        cells, p = ([], count_at + 4)
        for _k in range(n):
            col, cls_id = struct.unpack_from('<II', body, p)
            if body[p + 8:p + 13] == CELL_MARK:
                cells.append((p, col, cls_id, struct.unpack_from('<I', body, p + 13)[0]))
            else:
                assert values, (i, p, body[p + 8:p + 13].hex())
                assert body[p + 8:p + 10] == b'\x00\x00' and body[p + 10] in VALUE_SIZES, (i, p, body[p + 8:p + 13].hex())
                cells.append((p, col, cls_id, None))
            p += cell_size(body, p)
        out.append((i, count_at, cells))
        i = p
    return out

def remove_cells(body, drop, rows=None):
    body = bytearray(body)
    before = rows_of(bytes(body))
    removed = []
    for k, (row_start, count_at, cells) in reversed(list(enumerate(before))):
        if rows is not None and k not in rows:
            continue
        hits = [c for c in cells if drop(k, c)]
        for cell_start, col, cls_id, ref in reversed(hits):
            del body[cell_start:cell_start + cell_size(body, cell_start)]
            removed.append((k, row_start, col, cls_id, ref))
        if hits:
            n = struct.unpack_from('<I', body, count_at)[0]
            struct.pack_into('<I', body, count_at, n - len(hits))
    after = rows_of(bytes(body))
    assert len(after) == len(before)
    assert [len(c) for _s, _c, c in after] == [len(c) - sum((1 for r in removed if r[0] == k)) for k, (_s, _c, c) in enumerate(before)]
    return (bytes(body), removed)

def cell_bytes(body, cell):
    return bytes(body[cell[0]:cell[0] + cell_size(body, cell[0])])

def insert_cell(body, row, cell):
    assert len(cell) == CELL_SIZE and cell[8:13] == CELL_MARK, cell.hex()
    body = bytearray(body)
    before = rows_of(bytes(body))
    _start, count_at, cells = before[row]
    end = cells[-1][0] + cell_size(body, cells[-1][0]) if cells else count_at + 4
    body[end:end] = cell
    struct.pack_into('<I', body, count_at, len(cells) + 1)
    after = rows_of(bytes(body))
    assert [len(c) for _s, _c, c in after] == [len(c) + (k == row) for k, (_s, _c, c) in enumerate(before)]
    assert after[row][2][-1][1:] == struct.unpack_from('<II', cell) + struct.unpack_from('<I', cell, 13)
    return bytes(body)
BUILDCOLUMN = 914593288

def columns_of(body):
    n = struct.unpack_from('<I', body, 10)[0]
    rows = rows_of(body)
    rows_at = (rows[0][0] if rows else len(body)) - 4
    assert struct.unpack_from('<I', body, rows_at)[0] == len(rows), 'row count'
    mark, owner, starts, i = (struct.pack('<I', BUILDCOLUMN), b'\x01' + body[2:6], [], 14)
    while len(starts) < n:
        j = body.find(mark, i)
        assert 0 <= j < rows_at, (len(starts), n)
        starts.append(j - 4)
        i = j + 4
    assert not starts or starts[0] == 14, starts
    out = []
    for k, st in enumerate(starts):
        end = starts[k + 1] if k + 1 < n else rows_at
        o = body.rfind(owner, st, end)
        assert o > st, ('no owner reference in column', k)
        out.append((st, end, struct.unpack_from('<I', body, o - 4)[0], struct.unpack_from('<I', body, o + 13)[0]))
    return out

def add_column(body, like, new_id, new_uid, source=None):
    cols = columns_of(body)
    assert new_id not in {c[2] for c in cols}, f'column {new_id} exists'
    src = body if source is None else source
    st, end, _i, cls = next((c for c in columns_of(src) if c[2] == like))
    col = bytearray(src[st:end])
    if source is not None:
        assert bytes(col).count(src[2:6]) >= 1, 'the copied column names no owner table'
        col = bytearray(bytes(col).replace(src[2:6], body[2:6]))
    struct.pack_into('<I', col, 0, new_uid)
    o = bytes(col).rfind(b'\x01' + body[2:6])
    struct.pack_into('<I', col, o - 4, new_id)
    at = cols[-1][1]
    out = bytearray(body[:at]) + col + bytearray(body[at:])
    struct.pack_into('<I', out, 10, len(cols) + 1)
    assert [[c[1:] for c in r[2]] for r in rows_of(body)] == [[c[1:] for c in r[2]] for r in rows_of(bytes(out))]
    assert columns_of(bytes(out))[-1][2:] == (new_id, cls)
    return bytes(out)
