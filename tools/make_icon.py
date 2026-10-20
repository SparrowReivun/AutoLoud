"""Generate assets/autoloud.ico (and .png) without any external asset."""
from pathlib import Path
import struct, zlib

W = 256
def png(w, h, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")

bg = (79, 163, 255, 255); fg = (11, 18, 32, 255); tr = (0, 0, 0, 0)
bars = [(0.19, 0.30), (0.33, 0.50), (0.47, 0.68), (0.61, 0.44), (0.75, 0.24)]  # (x, height)
rows = []
for y in range(W):
    row = []
    for x in range(W):
        fx, fy = x / W, y / W
        lo, hi, r = 0.08, 0.92, 0.18
        inside = lo <= fx <= hi and lo <= fy <= hi
        if inside:
            cx = lo + r if fx < lo + r else (hi - r if fx > hi - r else None)
            cy = lo + r if fy < lo + r else (hi - r if fy > hi - r else None)
            if cx is not None and cy is not None and (fx - cx) ** 2 + (fy - cy) ** 2 > r * r:
                inside = False
        c = bg if inside else tr
        if inside:
            for bx, bh in bars:
                if bx <= fx <= bx + 0.09 and (0.78 - bh) <= fy <= 0.78:
                    c = fg
        row.extend(c)
    rows.append(row)
data = png(W, W, rows)
out = Path(__file__).resolve().parents[1] / "assets"
out.mkdir(exist_ok=True)
(out / "autoloud.png").write_bytes(data)
# ICO wrapping a single PNG entry (valid for Vista+)
ico = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(data), 22) + data
(out / "autoloud.ico").write_bytes(ico)
print("wrote", out / "autoloud.ico")
