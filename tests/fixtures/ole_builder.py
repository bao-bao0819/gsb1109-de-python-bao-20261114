"""Minimal, explicitly-laid-out OLE2 container writer used by the tests.

The purpose is not to create realistic documents but to place stream sectors
at chosen physical positions, so read planning can be checked against layouts
that a normal library writer would never produce (fragmented chains, shared
chains, gaps and mini-stream packing).
"""
import struct

MAGIC = b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1'
FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD
NOSTREAM = 0xFFFFFFFF
SECTOR = 512
MINI = 64


def _ceil_div(value, unit):
    return (value + unit - 1) // unit


def build_ole(path, streams, order, mini=()):
    """Write an OLE file with an explicit physical sector order.

    :param path: output path.
    :param streams: mapping name -> bytes.
    :param order: sequence of ``('stream', name)`` or ``'free'`` tokens giving
        the physical order of data sectors. Metadata sectors (ministream,
        MiniFAT, directory, FAT) are appended in that order.
    :param mini: names in ``streams`` that must be stored in the MiniFAT;
        by default streams smaller than the 4096-byte cutoff are mini streams.
    :returns: dict describing the resulting layout.
    """
    names = list(streams)
    data = {name: streams[name] for name in names}
    forced = set(name.lower() for name in mini)
    is_mini = {}
    for name in names:
        if name.lower() in forced:
            is_mini[name] = True
        else:
            is_mini[name] = len(data[name]) < 4096
    fat_streams = [name for name in names if not is_mini[name]]
    mini_streams = [name for name in names if is_mini[name]]

    data_nsec = {name: _ceil_div(len(data[name]), SECTOR)
                 for name in fat_streams}
    mini_start = {}
    mini_count = {}
    total_mini = 0
    for name in mini_streams:
        mini_start[name] = total_mini
        mini_count[name] = _ceil_div(len(data[name]), MINI)
        total_mini += mini_count[name]
    ministream_bytes = total_mini * MINI
    ministream_nsec = _ceil_div(ministream_bytes, SECTOR) if total_mini else 0
    minifat_nsec = _ceil_div(total_mini * 4, SECTOR) if total_mini else 0
    dir_nsec = _ceil_div(128 * (1 + len(names)), SECTOR)

    layout = []
    for token in order:
        kind = token[0] if isinstance(token, tuple) else token
        if kind == 'stream' and token[1] not in fat_streams:
            continue
        if kind in ('ministream', 'minifat', 'dir', 'fat'):
            continue
        layout.append(token)
    layout += ['ministream'] * ministream_nsec
    layout += ['minifat'] * minifat_nsec
    layout += ['dir'] * dir_nsec

    nbase = (sum(data_nsec.values()) + ministream_nsec + minifat_nsec
             + dir_nsec + layout.count('free'))
    nfat = 1
    for _ in range(8):
        nfat = _ceil_div(nbase + nfat, 128)
    layout += ['fat'] * nfat

    chains = {name: [] for name in fat_streams}
    ministream_chain = []
    minifat_chain = []
    dir_chain = []
    fat_chain = []
    for position, token in enumerate(layout):
        kind = token[0] if isinstance(token, tuple) else token
        if kind == 'stream':
            chains[token[1]].append(position)
        elif kind == 'ministream':
            ministream_chain.append(position)
        elif kind == 'minifat':
            minifat_chain.append(position)
        elif kind == 'dir':
            dir_chain.append(position)
        elif kind == 'fat':
            fat_chain.append(position)
        elif kind != 'free':
            raise ValueError(kind)
    total = len(layout)
    for name in fat_streams:
        assert len(chains[name]) == data_nsec[name]
    assert len(ministream_chain) == ministream_nsec
    assert len(minifat_chain) == minifat_nsec
    assert len(dir_chain) == dir_nsec
    assert len(fat_chain) == nfat

    fat = [FREESECT] * total
    for chain in list(chains.values()) + [ministream_chain, minifat_chain,
                                          dir_chain]:
        for index, sector in enumerate(chain):
            fat[sector] = (chain[index + 1] if index + 1 < len(chain)
                           else ENDOFCHAIN)
    for sector in fat_chain:
        fat[sector] = FATSECT

    minifat = [FREESECT] * (minifat_nsec * 128 if minifat_nsec else 0)
    for name in mini_streams:
        start = mini_start[name]
        count = mini_count[name]
        for index in range(count):
            slot = start + index
            minifat[slot] = (ENDOFCHAIN if index == count - 1 else slot + 1)

    def entry(name, entry_type, start, size, right=NOSTREAM,
              child=NOSTREAM):
        raw = name.encode('utf-16-le')
        return struct.pack(
            '<64sHBBIII16sIQQIII', raw, len(raw) + 2, entry_type, 1,
            NOSTREAM, right, child, b'\x00' * 16, 0, 0, 0, start,
            size & 0xFFFFFFFF, (size >> 32) & 0xFFFFFFFF)

    dirents = [entry('Root Entry', 5,
                     ministream_chain[0] if ministream_chain else ENDOFCHAIN,
                     ministream_bytes, child=1 if names else NOSTREAM)]
    for index, name in enumerate(names):
        right = index + 2 if index + 1 < len(names) else NOSTREAM
        start = mini_start[name] if is_mini[name] else chains[name][0]
        dirents.append(entry(name, 2, start, len(data[name]), right))
    dirbytes = b''.join(dirents)
    dirbytes += b'\x00' * (dir_nsec * SECTOR - len(dirbytes))

    sectors = {}
    for name, chain in chains.items():
        for index, sector in enumerate(chain):
            chunk = data[name][index * SECTOR:(index + 1) * SECTOR]
            sectors[sector] = chunk + b'\x00' * (SECTOR - len(chunk))
    minibytes = bytearray(ministream_bytes)
    for name in mini_streams:
        start = mini_start[name] * MINI
        minibytes[start:start + len(data[name])] = data[name]
    for index, sector in enumerate(ministream_chain):
        chunk = bytes(minibytes[index * SECTOR:(index + 1) * SECTOR])
        sectors[sector] = chunk + b'\x00' * (SECTOR - len(chunk))
    minifat_bytes = b''.join(struct.pack('<I', value) for value in minifat)
    minifat_bytes += b'\x00' * (minifat_nsec * SECTOR - len(minifat_bytes))
    for index, sector in enumerate(minifat_chain):
        sectors[sector] = minifat_bytes[index * SECTOR:(index + 1) * SECTOR]
    for index, sector in enumerate(dir_chain):
        sectors[sector] = dirbytes[index * SECTOR:(index + 1) * SECTOR]
    fat_bytes = b''.join(struct.pack('<I', value) for value in fat)
    fat_bytes += b'\x00' * (nfat * SECTOR - len(fat_bytes))
    for index, sector in enumerate(fat_chain):
        sectors[sector] = fat_bytes[index * SECTOR:(index + 1) * SECTOR]
    for index in range(total):
        sectors.setdefault(index, b'\x00' * SECTOR)

    header = bytearray(SECTOR)
    header[0:8] = MAGIC
    struct.pack_into('<HHHHHH', header, 24, 0x3E, 3, 0xFFFE, 9, 6, 0)
    struct.pack_into(
        '<IIIIIIIII', header, 40, 0, nfat, dir_chain[0], 0, 0x1000,
        minifat_chain[0] if minifat_chain else ENDOFCHAIN, minifat_nsec,
        ENDOFCHAIN, 0)
    struct.pack_into('<109I', header, 76,
                     *(fat_chain + [FREESECT] * (109 - len(fat_chain))))
    with open(path, 'wb') as handle:
        handle.write(bytes(header))
        for index in range(total):
            handle.write(sectors[index])
    return {'fat_chain': fat_chain, 'dir_chain': dir_chain,
            'chains': chains, 'ministream_chain': ministream_chain,
            'minifat_chain': minifat_chain, 'mini_start': mini_start,
            'total': total}
