"""Plain DDS textures written from nothing: one colour (DXT1) or fully transparent (DXT5), with
every mipmap level. A file at a texture's path inside the map archive is used in place of the
game's (the solid skyboxes, sky.py; the status strip's white texture, map/prepare.py)."""

import struct

from heroes_capture.common.js_json import js_round


def block_dds(four_cc: str, block: bytes, width: int, height: int) -> bytes:
    """A DDS of one repeated 4x4 block with a full mip chain, the layout of the game's own skybox
    textures (1024x512, 11 levels; the streaming `.lvl0` copy is 64x32, 7 levels)."""
    levels = []
    w, h = width, height
    while True:
        levels.append(block * (-(-w // 4) * -(-h // 4)))
        if w == 1 and h == 1:
            break
        w, h = max(1, w >> 1), max(1, h >> 1)
    header = bytearray(128)
    header[0:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)  # header size
    struct.pack_into("<I", header, 8, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000)  # caps, height, width, pixel format, mipmap count, linear size
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, len(levels[0]))
    struct.pack_into("<I", header, 28, len(levels))
    struct.pack_into("<I", header, 76, 32)  # pixel format size
    struct.pack_into("<I", header, 80, 0x4)  # four-character code follows
    header[84:88] = four_cc.encode("ascii")
    struct.pack_into("<I", header, 108, 0x8 | 0x400000 | 0x1000)  # complex, mipmaps, texture
    return bytes(header) + b"".join(levels)


def solid_dds(rgb, width: int = 1024, height: int = 512) -> bytes:
    """A DXT1 DDS of one colour."""
    r, g, b = rgb
    c565 = ((js_round(r * 31 / 255) << 11) | (js_round(g * 63 / 255) << 5) | js_round(b * 31 / 255)) & 0xFFFF
    block = struct.pack("<HH", c565, c565) + bytes(4)  # both colours the same, every index 0: the whole block is c565
    return block_dds("DXT1", block, width, height)


def clear_dds(width: int = 1024, height: int = 512) -> bytes:
    """A DXT5 DDS that is fully transparent (every texel alpha 0)."""
    return block_dds("DXT5", bytes(16), width, height)  # alpha0 = alpha1 = 0, every index 0; colour block all zero
