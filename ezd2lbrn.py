#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ezd2lbrn - EZCAD2 (.ezd) -> LightBurn (.lbrn2) Konverter, offline.

Texte werden als echte LightBurn-Textobjekte geschrieben (bearbeitbar),
Position, Hoehe, Fett, Ausrichtung, Zeilen- und Zeichenabstand werden aus der
EZD-Datei uebernommen. Schraffuren werden zu Fill-Layern (Scan), Konturen zu
Line-Layern (Cut). EZCAD-Stifte werden zu LightBurn-Layern.

Aufruf:
    python ezd2lbrn.py                  -> GUI
    python ezd2lbrn.py datei.ezd [...]  -> Kommandozeile (siehe --help)

Copyright (c) 2026 Christopher Schmick, iot-simply.com
Open Source unter der MIT-Lizenz (Lizenztext: Schaltflaeche "Lizenzen" im
Programm oder  python ezd2lbrn.py --lizenzen). Ohne Gewaehrleistung.

EZCAD ist eine Marke von Beijing JCZ Technology, LightBurn eine Marke von
LightBurn Software LLC. ezd2lbrn ist ein unabhaengiges Projekt und steht in
keiner Verbindung zu diesen Unternehmen, es wird von ihnen weder unterstuetzt
noch gebilligt. Die Namen werden nur genannt, um die Kompatibilitaet zu
beschreiben.

Der EZD-Leseteil (Huffman-Dekompression, Objektstruktur, Feldbelegung) basiert
auf dem EZD-Parser von MeerK40t (https://github.com/meerk40t/meerk40t),
Copyright (c) 2021 meerk40t, MIT-Lizenz - vollstaendiger Lizenztext im Programm
(Schaltflaeche "Lizenzen", Konstante LICENSES_Z_B64, Aufruf --lizenzen).
Er wurde fuer ezd2lbrn neu geschrieben und erweitert.
"""

import argparse
import math
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET
from io import BytesIO
from xml.sax.saxutils import quoteattr

VERSION = "1.7"
APP_AUTHOR = "Christopher Schmick"
APP_BRAND = "iot-simply"
APP_URL = "https://iot-simply.com"
APP_YEAR = "2026"

# ---------------------------------------------------------------------------
#  EZD-Parser
# ---------------------------------------------------------------------------


class EzdError(Exception):
    pass


def _read_struct(f, max_items=100000, max_size=64 * 1024 * 1024):
    """count:int32, dann count x (len:int32, bytes)."""
    raw = f.read(4)
    if len(raw) != 4:
        raise EzdError("Unerwartetes Dateiende")
    (count,) = struct.unpack("<i", raw)
    if count < 0 or count > max_items:
        raise EzdError("Ungueltige Strukturgroesse %d" % count)
    out = []
    for _ in range(count):
        raw = f.read(4)
        if len(raw) != 4:
            raise EzdError("Unerwartetes Dateiende")
        (ln,) = struct.unpack("<i", raw)
        if ln < 0 or ln > max_size:
            raise EzdError("Ungueltige Feldlaenge %d" % ln)
        b = f.read(ln)
        if len(b) != ln:
            raise EzdError("Unerwartetes Dateiende")
        out.append(b)
    return out


def _v(b, kind=None):
    """Feld nach Laenge/Typ interpretieren."""
    if b is None:
        return None
    n = len(b)
    if kind == "str":
        return b.decode("utf-16-le", "replace").split("\x00")[0]
    if n == 2:
        return struct.unpack("<H", b)[0]
    if n == 4:
        return struct.unpack("<i", b)[0]
    if n == 8:
        return struct.unpack("<d", b)[0]
    if n == 16:
        return struct.unpack("<2d", b)
    if n == 72:
        return struct.unpack("<9d", b)
    return b


def _arg(args, i, kind=None, default=None):
    if i < len(args):
        try:
            return _v(args[i], kind)
        except Exception:
            return default
    return default


def huffman_decode(f, out_len):
    n = struct.unpack("<H", f.read(2))[0]
    table = []
    maxlen = 0
    for _ in range(n):
        ch, bits, ln = struct.unpack("<BIH", f.read(7))
        if ln <= 0 or ln > 24:
            continue
        table.append((ch, bits & ((1 << ln) - 1), ln))
        maxlen = max(maxlen, ln)
    if not table:
        raise EzdError("Huffman-Tabelle leer")
    # Lookup-Tabelle fuer maxlen Bits
    size = 1 << maxlen
    lut_ch = bytearray(size)
    lut_ln = bytearray(size)
    for ch, code, ln in table:
        start = code << (maxlen - ln)
        for k in range(start, start + (1 << (maxlen - ln))):
            lut_ch[k] = ch
            lut_ln[k] = ln
    data = f.read()
    out = bytearray()
    acc = 0
    nbits = 0
    pos = 0
    total = len(data)
    mask = size - 1
    append = out.append
    while len(out) < out_len:
        while nbits < maxlen:
            if pos < total:
                acc = (acc << 8) | data[pos]
                pos += 1
            else:
                acc <<= 8
            nbits += 8
        idx = (acc >> (nbits - maxlen)) & mask
        ln = lut_ln[idx]
        if ln == 0:
            raise EzdError("Huffman-Dekodierung fehlgeschlagen")
        append(lut_ch[idx])
        nbits -= ln
        acc &= (1 << nbits) - 1
        if pos >= total and nbits <= 0:
            break
    return bytes(out)


class Pen:
    def __init__(self, idx, args):
        self.index = idx
        self.color = _arg(args, 0, default=0)
        self.name = _arg(args, 1, "str", "") or ""
        self.passes = _arg(args, 4, default=1) or 1
        self.speed = _arg(args, 5, default=100.0)
        self.power = _arg(args, 6, default=50.0)
        self.frequency = _arg(args, 7, default=30000)
        self.pulse_width = _arg(args, 8, default=None)
        self.start_tc = _arg(args, 9, default=None)
        self.end_tc = _arg(args, 10, default=None)
        self.polygon_tc = _arg(args, 11, default=None)
        self.jump_speed = _arg(args, 12, default=None)
        self.wobble = _arg(args, 26, default=0)


class Obj:
    """Gemeinsamer Objektkopf (15 Felder)."""

    container = False
    kind = "obj"

    def __init__(self, f, ezd):
        h = _read_struct(f)
        self.pen = _arg(h, 0, default=0) or 0
        self.type = _arg(h, 1, default=0)
        self.state = _arg(h, 2, default=0) or 0
        self.hidden = bool(self.state & 0x01)
        self.label = _arg(h, 3, "str", "") or ""
        self.array_x = _arg(h, 9, default=1)
        self.array_y = _arg(h, 10, default=1)
        self.position = _arg(h, 13, default=(0.0, 0.0))
        self.children = []
        if self.container:
            (count,) = struct.unpack("<i", f.read(4))
            if count < 0 or count > 100000:
                raise EzdError("Ungueltige Kinderanzahl")
            for _ in range(count):
                o = parse_object(f, ezd, strict=True)
                if o is None:
                    break
                self.children.append(o)


class Group(Obj):
    container = True
    kind = "group"


class Combine(Obj):
    container = True
    kind = "combine"


class VectorFile(Obj):
    container = True
    kind = "vectorfile"

    def __init__(self, f, ezd):
        super().__init__(f, ezd)
        a = _read_struct(f)
        self.path = _arg(a, 0, "str", "")


class Curve(Obj):
    kind = "curve"

    def __init__(self, f, ezd):
        super().__init__(f, ezd)
        count, closed = struct.unpack("<2I", f.read(8))
        if count > 10000000:
            raise EzdError("Ungueltige Kurve")
        self.closed = bool(closed)
        self.segments = []
        for _ in range(count):
            _u1, ctype, _u2, _u3 = struct.unpack("<BB2H", f.read(6))
            if ctype == 0:
                f.read(40)
                continue
            (n,) = struct.unpack("<i", f.read(4))
            if n < 0 or n > 10000000:
                raise EzdError("Ungueltige Punktanzahl")
            c = struct.unpack("<%dd" % (2 * n), f.read(16 * n))
            pts = [(c[i], c[i + 1]) for i in range(0, len(c), 2)]
            self.segments.append((ctype, pts))


class Rect(Obj):
    kind = "rect"

    def __init__(self, f, ezd):
        super().__init__(f, ezd)
        a = _read_struct(f)
        self.p0 = _arg(a, 0, default=(0, 0))
        self.p1 = _arg(a, 1, default=(0, 0))
        self.round = [_arg(a, i, default=0) or 0 for i in range(2, 6)]
        self.matrix = _arg(a, 7, default=None)


class Circle(Obj):
    kind = "circle"

    def __init__(self, f, ezd):
        super().__init__(f, ezd)
        a = _read_struct(f)
        self.center = _arg(a, 0, default=(0, 0))
        self.radius = _arg(a, 1, default=0)
        self.matrix = _arg(a, 5, default=None)


class Ellipse(Obj):
    kind = "ellipse"

    def __init__(self, f, ezd):
        super().__init__(f, ezd)
        a = _read_struct(f)
        self.p0 = _arg(a, 1, default=(0, 0))
        self.p1 = _arg(a, 2, default=(0, 0))
        self.start_angle = _arg(a, 3, default=0)
        self.end_angle = _arg(a, 4, default=0)
        self.matrix = _arg(a, 6, default=None)


class Polygon(Obj):
    kind = "polygon"

    def __init__(self, f, ezd):
        super().__init__(f, ezd)
        a = _read_struct(f)
        self.p0 = _arg(a, 1, default=(0, 0))
        self.p1 = _arg(a, 2, default=(0, 0))
        self.sides = _arg(a, 7, default=6) or 6
        self.matrix = _arg(a, 9, default=None)


class Spiral(Obj):
    container = True
    kind = "spiral"

    def __init__(self, f, ezd):
        super().__init__(f, ezd)
        _read_struct(f)
        self.cache = Group(f, ezd)


class Simple(Obj):
    """Timer, Ein-/Ausgang, Achsen: nur Parameter, keine Geometrie."""

    kind = "control"

    def __init__(self, f, ezd):
        super().__init__(f, ezd)
        _read_struct(f)


class Image(Obj):
    kind = "image"

    def __init__(self, f, ezd):
        super().__init__(f, ezd)
        a = _read_struct(f)
        f.read(2)  # "BM"
        (size,) = struct.unpack("<i", f.read(4))
        if size < 6 or size > 512 * 1024 * 1024:
            raise EzdError("Ungueltiges Bild")
        f.read(size - 6)
        self.width = _arg(a, 5, default=0)
        self.height = _arg(a, 4, default=0)


class Text(Obj):
    kind = "text"

    def __init__(self, f, ezd):
        super().__init__(f, ezd)
        a = _read_struct(f)
        self.height = _arg(a, 1, default=1.0) or 1.0
        self.width = _arg(a, 2, default=None)
        self.line_gap = _arg(a, 6, default=0.0) or 0.0
        self.ref = _arg(a, 7, default=(0.0, 0.0))
        if not isinstance(self.ref, tuple):
            self.ref = (0.0, 0.0)
        self.align = _arg(a, 8, default=0)
        m = _arg(a, 9, default=None)
        self.matrix = m if isinstance(m, tuple) and len(m) == 9 else None
        self.text = _arg(a, 10, "str", "") or ""
        self.font = _arg(a, 18, "str", "") or "Arial"
        self.bold = bool(_arg(a, 97, default=0))
        self.variable = False
        (count,) = struct.unpack("<i", f.read(4))
        if count < 0 or count > 10000:
            raise EzdError("Ungueltiger Text")
        for _ in range(count):
            f.read(2)
            _read_struct(f)
            _read_struct(f)
            self.variable = True
        f.read(4)


class Hatch(Obj):
    container = True
    kind = "hatch"

    def __init__(self, f, ezd):
        super().__init__(f, ezd)
        a = _read_struct(f)
        self.supported = len(a) >= 42
        if self.supported:
            self.mark_contours = bool(_arg(a, 0, default=0))
            self.h1_on = bool(_arg(a, 1, default=1))
            self.h1_pen = _arg(a, 2, default=0)
            self.h1_type = _arg(a, 3, default=0) or 0
            self.h1_space = _arg(a, 5, default=0.05)
            self.h1_angle = _arg(a, 8, default=0.0)
            self.h2_on = bool(_arg(a, 9, default=0))
            self.h2_angle = _arg(a, 16, default=90.0)
            self.h2_space = _arg(a, 13, default=0.05)
            self.h3_on = bool(_arg(a, 20, default=0))
            self.h1_loops = _arg(a, 32, default=0) or 0
        else:
            self.mark_contours = False
            self.h1_on, self.h1_pen, self.h1_type = True, self.pen, 0
            self.h1_space, self.h1_angle = 0.05, 0.0
            self.h2_on, self.h2_angle, self.h2_space, self.h3_on = False, 90.0, 0.05, False
            self.h1_loops = 0
        # zwischengespeicherte Schraffurlinien (ohne Typmarker)
        self.cache = None
        pos = f.tell()
        b = f.read(4)
        if len(b) == 4 and struct.unpack("<i", b)[0] == 15:
            f.seek(pos)
            try:
                self.cache = Group(f, ezd)
            except Exception:
                f.seek(pos)
        else:
            f.seek(pos)

    @property
    def crosshatch(self):
        return bool(self.h1_type & 0x400) or (self.h2_on and abs(((self.h2_angle - self.h1_angle) % 180) - 90) < 1)


OBJECT_MAP = {
    1: Curve, 3: Rect, 4: Circle, 5: Ellipse, 6: Polygon,
    0x10: Group, 0x20: Hatch, 0x30: Combine, 0x40: Image, 0x50: VectorFile,
    0x60: Spiral, 0x800: Text,
    0x2000: Simple, 0x3000: Simple, 0x4000: Simple, 0x5000: Simple, 0x6000: Simple,
}


def parse_object(f, ezd, strict=False):
    start = f.tell()
    b = f.read(4)
    if len(b) != 4:
        return None
    (t,) = struct.unpack("<i", b)
    if t == 0:
        return None
    cls = OBJECT_MAP.get(t)
    if cls is None:
        if strict:
            f.seek(start)
            return None
        ezd.warn("Unbekannter Objekttyp 0x%X bei Offset %d - ueberspringe" % (t, start))
        return _resync(f, ezd, start + 1)
    try:
        return cls(f, ezd)
    except Exception as ex:
        if strict:
            raise
        ezd.warn("Objekt Typ 0x%X nicht lesbar (%s) - ueberspringe" % (t, ex))
        return _resync(f, ezd, start + 4)


def _resync(f, ezd, pos):
    data_len = len(f.getbuffer())
    while pos + 8 < data_len:
        f.seek(pos)
        t, n = struct.unpack("<ii", f.read(8))
        if t in OBJECT_MAP and n == 15:
            f.seek(pos)
            try:
                return OBJECT_MAP[t](f, ezd)
            except Exception:
                pass
        pos += 1
    f.seek(data_len)
    return None


class EzdFile:
    def __init__(self, path):
        self.path = path
        self.warnings = []
        self.pens = []
        self.fonts = []
        self.objects = []
        with open(path, "rb") as f:
            self._parse(f)

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)

    def _parse(self, f):
        magic = f.read(16)
        if magic.decode("utf-16-le", "replace") != "EZCADUNI":
            raise EzdError("Keine EZCAD2-Datei (EZCADUNI-Kennung fehlt)")
        f.seek(16 + 4 + 4 + 60 * 3 + 140)
        loc = struct.unpack("<7i", f.read(28))
        _preview, _v1, pens, font, _v4, vectors, _pre = loc
        if pens:
            f.seek(pens)
            n, seek = struct.unpack("<ii", f.read(8))
            f.seek(seek)
            for i in range(max(0, min(n, 256))):
                try:
                    self.pens.append(Pen(i, _read_struct(f)))
                except Exception:
                    break
        if font:
            f.seek(font)
            (n,) = struct.unpack("<i", f.read(4))
            for _ in range(max(0, min(n, 1000))):
                self.fonts.append(f.read(100).decode("utf-16-le", "replace").split("\x00")[0])
        if not vectors:
            return
        f.seek(vectors)
        out_len = struct.unpack("<IIIII", f.read(20))[0]
        data = huffman_decode(f, out_len)
        bio = BytesIO(data)
        while bio.tell() < len(data) - 4:
            pos = bio.tell()
            o = parse_object(bio, self)
            if o is None:
                if bio.tell() <= pos:
                    break
                continue
            self.objects.append(o)

    def pen(self, i):
        if 0 <= i < len(self.pens):
            return self.pens[i]
        return None


# ---------------------------------------------------------------------------
#  Geometrie
# ---------------------------------------------------------------------------


class Vertex:
    __slots__ = ("x", "y", "c0", "c1")

    def __init__(self, x, y):
        self.x, self.y = x, y
        self.c0 = None  # Kontrollpunkt ausgehend
        self.c1 = None  # Kontrollpunkt eingehend


class Contour:
    def __init__(self):
        self.v = []
        self.prims = []  # (typ, i, j)
        self.closed = False

    def start(self, p):
        self.v.append(Vertex(*p))

    def line(self, p):
        self.v.append(Vertex(*p))
        self.prims.append(("L", len(self.v) - 2, len(self.v) - 1))

    def cubic(self, c1, c2, p):
        self.v[-1].c0 = c1
        nv = Vertex(*p)
        nv.c1 = c2
        self.v.append(nv)
        self.prims.append(("B", len(self.v) - 2, len(self.v) - 1))

    def close(self):
        if len(self.v) >= 2:
            a, b = self.v[0], self.v[-1]
            if abs(a.x - b.x) < 1e-6 and abs(a.y - b.y) < 1e-6:
                t, i, _ = self.prims[-1]
                if b.c1 is not None:
                    a.c1 = b.c1
                self.v.pop()
                self.prims[-1] = (t, i, 0)
            else:
                self.prims.append(("L", len(self.v) - 1, 0))
        self.closed = True

    def transform(self, fn):
        for v in self.v:
            v.x, v.y = fn((v.x, v.y))
            if v.c0:
                v.c0 = fn(v.c0)
            if v.c1:
                v.c1 = fn(v.c1)

    def sample(self):
        """Punkte auf der Kontur (Ecken + Zwischenpunkte der Beziers) fuer Begrenzungsrahmen."""
        for v in self.v:
            yield (v.x, v.y)
        for t_, i, j in self.prims:
            if t_ == "B":
                a, b = self.v[i], self.v[j]
                c0 = a.c0 or (a.x, a.y)
                c1 = b.c1 or (b.x, b.y)
                for p in bezier_samples((a.x, a.y), c0, c1, (b.x, b.y)):
                    yield p

    def points(self):
        for v in self.v:
            yield (v.x, v.y)
            if v.c0:
                yield v.c0
            if v.c1:
                yield v.c1


def bezier_samples(p0, p1, p2, p3, n=8):
    for k in range(1, n):
        t = k / float(n)
        u = 1 - t
        yield (u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
               u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1])


def _mat_fn(m):
    if not m:
        return lambda p: p
    a, b, _, c, d, _, e, f_, _ = m
    return lambda p: (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f_)


def _dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def curve_contours(obj):
    out = []
    cur = None
    for ctype, pts in obj.segments:
        if not pts:
            continue
        if cur is None or _dist((cur.v[-1].x, cur.v[-1].y), pts[0]) > 1e-6:
            if cur is not None and len(cur.v) > 1:
                out.append(cur)
            cur = Contour()
            cur.start(pts[0])
        if ctype == 1:
            for p in pts[1:]:
                cur.line(p)
        elif ctype == 3:
            for i in range(1, len(pts) - 2, 3):
                cur.cubic(pts[i], pts[i + 1], pts[i + 2])
        elif ctype == 2:
            for i in range(1, len(pts) - 1, 2):
                p0 = (cur.v[-1].x, cur.v[-1].y)
                q, p = pts[i], pts[i + 1]
                c1 = (p0[0] + 2 / 3 * (q[0] - p0[0]), p0[1] + 2 / 3 * (q[1] - p0[1]))
                c2 = (p[0] + 2 / 3 * (q[0] - p[0]), p[1] + 2 / 3 * (q[1] - p[1]))
                cur.cubic(c1, c2, p)
        else:
            for p in pts[1:]:
                cur.line(p)
    if cur is not None and len(cur.v) > 1:
        out.append(cur)
    if obj.closed:
        for c in out:
            c.close()
    else:
        for c in out:
            a, b = c.v[0], c.v[-1]
            if len(c.v) > 2 and abs(a.x - b.x) < 1e-6 and abs(a.y - b.y) < 1e-6:
                c.close()
    return out


def ellipse_contour(cx, cy, rx, ry):
    k = 0.5522847498
    c = Contour()
    c.start((cx + rx, cy))
    c.cubic((cx + rx, cy + k * ry), (cx + k * rx, cy + ry), (cx, cy + ry))
    c.cubic((cx - k * rx, cy + ry), (cx - rx, cy + k * ry), (cx - rx, cy))
    c.cubic((cx - rx, cy - k * ry), (cx - k * rx, cy - ry), (cx, cy - ry))
    c.cubic((cx + k * rx, cy - ry), (cx + rx, cy - k * ry), (cx + rx, cy))
    c.close()
    return c


def object_contours(o, ezd):
    """Alle Konturen eines Objekts (rekursiv), in EZCAD-Weltkoordinaten."""
    k = o.kind
    if getattr(o, "hidden", False):
        return []
    if k == "curve":
        return curve_contours(o)
    if k == "rect":
        x0, y0 = o.p0
        x1, y1 = o.p1
        c = Contour()
        c.start((x0, y0))
        c.line((x1, y0))
        c.line((x1, y1))
        c.line((x0, y1))
        c.close()
        if any(abs(r) > 1e-9 for r in o.round):
            ezd.warn("Rechteck mit abgerundeten Ecken wurde mit eckigen Ecken uebernommen")
        c.transform(_mat_fn(o.matrix))
        return [c]
    if k == "circle":
        c = ellipse_contour(o.center[0], o.center[1], o.radius, o.radius)
        c.transform(_mat_fn(o.matrix))
        return [c]
    if k == "ellipse":
        (x0, y0), (x1, y1) = o.p0, o.p1
        c = ellipse_contour((x0 + x1) / 2, (y0 + y1) / 2, abs(x1 - x0) / 2, abs(y1 - y0) / 2)
        if abs((o.end_angle or 0) - (o.start_angle or 0)) > 1e-6 and abs(abs((o.end_angle or 0) - (o.start_angle or 0)) - 2 * math.pi) > 1e-3:
            ezd.warn("Ellipsenbogen wurde als volle Ellipse uebernommen")
        c.transform(_mat_fn(o.matrix))
        return [c]
    if k == "polygon":
        (x0, y0), (x1, y1) = o.p0, o.p1
        cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
        step = 2 * math.pi / max(3, o.sides)
        c = Contour()
        th = step / 2
        for i in range(max(3, o.sides)):
            p = (cx + math.cos(th) * rx, cy + math.sin(th) * ry)
            c.start(p) if i == 0 else c.line(p)
            th += step
        c.close()
        c.transform(_mat_fn(o.matrix))
        return [c]
    if k == "spiral":
        return [c for ch in o.cache.children for c in object_contours(ch, ezd)] if o.cache else []
    if k == "text":
        return []
    if o.container:
        out = []
        for ch in o.children:
            out.extend(object_contours(ch, ezd))
        return out
    return []


def bbox(points):
    xs, ys = [], []
    for x, y in points:
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------
#  Schriften (fuer Zeilenabstand / Zeichenabstand)
# ---------------------------------------------------------------------------

ARIAL_K = (1854 + 434) / 2048.0  # (winAscent + winDescent) / unitsPerEm
ARIAL_AVG = {False: 904 / 2048.0, True: 983 / 2048.0}  # OS/2 xAvgCharWidth Arial / Arial Bold


class FontMetrics:
    """Liest Metriken einer installierten Schrift (benoetigt fontTools)."""

    _index = None

    def __init__(self, path, number=0):
        from fontTools.ttLib import TTFont
        from fontTools.pens.boundsPen import BoundsPen

        self._BoundsPen = BoundsPen
        self.font = TTFont(path, fontNumber=number, lazy=True)
        self.upm = self.font["head"].unitsPerEm
        os2 = self.font["OS/2"] if "OS/2" in self.font else None
        if os2 is not None and (os2.usWinAscent + os2.usWinDescent) > 0:
            self.k = (os2.usWinAscent + os2.usWinDescent) / float(self.upm)
        else:
            hh = self.font["hhea"]
            self.k = (hh.ascent - hh.descent) / float(self.upm)
        self.avg = (os2.xAvgCharWidth / float(self.upm)) if (os2 is not None and os2.xAvgCharWidth > 0) else None
        self.cmap = self.font.getBestCmap() or {}
        self.hmtx = self.font["hmtx"]
        self.gs = self.font.getGlyphSet()
        self._bounds = {}

    def _gb(self, name):
        if name not in self._bounds:
            pen = self._BoundsPen(self.gs)
            try:
                self.gs[name].draw(pen)
                self._bounds[name] = pen.bounds
            except Exception:
                self._bounds[name] = None
        return self._bounds[name]

    def ink_width(self, s, size):
        """Breite der sichtbaren Glyphen bei Schriftgroesse size (ohne Kerning)."""
        x = 0
        lo = hi = None
        for ch in s:
            name = self.cmap.get(ord(ch), ".notdef")
            try:
                adv = self.hmtx[name][0]
            except KeyError:
                adv = self.upm // 2
            b = self._gb(name)
            if b:
                a, c = x + b[0], x + b[2]
                lo = a if lo is None else min(lo, a)
                hi = c if hi is None else max(hi, c)
            x += adv
        if lo is None:
            return None
        return (hi - lo) * size / self.upm

    # ---- Suche installierter Schriften ----
    @classmethod
    def font_dirs(cls):
        dirs = []
        windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
        if windir:
            dirs.append(os.path.join(windir, "Fonts"))
        la = os.environ.get("LOCALAPPDATA")
        if la:
            dirs.append(os.path.join(la, "Microsoft", "Windows", "Fonts"))
        dirs += ["/Library/Fonts", "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts"),
                 "/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                 os.path.expanduser("~/.local/share/fonts")]
        return [d for d in dirs if os.path.isdir(d)]

    @classmethod
    def build_index(cls):
        if cls._index is not None:
            return cls._index
        cls._index = {}
        try:
            from fontTools.ttLib import TTFont, TTCollection
        except ImportError:
            return cls._index
        for d in cls.font_dirs():
            for root, _dirs, files in os.walk(d):
                for fn in files:
                    low = fn.lower()
                    if not low.endswith((".ttf", ".otf", ".ttc")):
                        continue
                    p = os.path.join(root, fn)
                    try:
                        if low.endswith(".ttc"):
                            fonts = list(enumerate(TTCollection(p, lazy=True).fonts))
                        else:
                            fonts = [(0, TTFont(p, lazy=True))]
                        for num, ft in fonts:
                            name = ft["name"]
                            fam = name.getDebugName(1) or ""
                            sub = (name.getDebugName(2) or "").lower()
                            bold = "bold" in sub or "fett" in sub
                            italic = "italic" in sub or "oblique" in sub or "kursiv" in sub
                            key = (fam.lower(), bold, italic)
                            cls._index.setdefault(key, (p, num))
                    except Exception:
                        continue
        return cls._index

    _cache = {}

    @classmethod
    def lookup(cls, family, bold=False, italic=False):
        key = (family.lower(), bold, italic)
        if key in cls._cache:
            return cls._cache[key]
        idx = cls.build_index()
        hit = idx.get(key) or idx.get((family.lower(), bold, False)) or idx.get((family.lower(), False, False))
        fm = None
        if hit:
            try:
                fm = FontMetrics(*hit)
            except Exception:
                fm = None
        cls._cache[key] = fm
        return fm


# ---------------------------------------------------------------------------
#  LightBurn-Vorlage: Begrenzungsrahmen des Inhalts
# ---------------------------------------------------------------------------

_NUM = r"(-?[0-9.]+(?:[eE][-+]?[0-9]+)?)"
_VERT_RE = re.compile(
    "V" + _NUM + " " + _NUM + "(?:c0x" + _NUM + "(?:c0y" + _NUM + ")?)?(?:c1x" + _NUM + "(?:c1y" + _NUM + ")?)?")
_PRIM_RE = re.compile(r"([LB])(\d+) (\d+)")


def _xf_mul(A, B):
    """A nach B (B zuerst anwenden). Format (a b c d e f)."""
    a1, b1, c1, d1, e1, f1 = A
    a2, b2, c2, d2, e2, f2 = B
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def _xf_of(el):
    x = el.find("XForm")
    try:
        v = [float(t) for t in (x.text or "").split()] if x is not None else []
    except ValueError:
        v = []
    return tuple(v[:6]) if len(v) >= 6 else (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def template_extent(path):
    """Liefert (bbox, hinweise) des sichtbaren Inhalts einer .lbrn2-Datei."""
    root = ET.parse(path).getroot()
    pts = []
    notes = []
    verts_by_id, prims_by_id = {}, {}

    def ap(xf, x, y):
        a, b, c, d, e, f_ = xf
        pts.append((a * x + c * y + e, b * x + d * y + f_))

    def parse_verts(txt):
        out = []
        for m in _VERT_RE.finditer(txt or ""):
            x, y, c0x, c0y, c1x, c1y = m.groups()
            c0 = (float(c0x), float(c0y)) if c0y is not None else None
            c1 = (float(c1x), float(c1y)) if c1y is not None else None
            out.append((float(x), float(y), c0, c1))
        return out

    def path_points(el, xf):
        vl, pl = el.find("VertList"), el.find("PrimList")
        vid, pid = el.get("VertID"), el.get("PrimID")
        verts = parse_verts(vl.text) if vl is not None else verts_by_id.get(vid, [])
        if vl is not None and vid:
            verts_by_id[vid] = verts
        ptxt = pl.text if pl is not None else prims_by_id.get(pid, "")
        if pl is not None and pid:
            prims_by_id[pid] = ptxt
        for x, y, _c0, _c1 in verts:
            ap(xf, x, y)
        for t_, i, j in _PRIM_RE.findall(ptxt or ""):
            i, j = int(i), int(j)
            if t_ == "B" and i < len(verts) and j < len(verts):
                a, b = verts[i], verts[j]
                for q in bezier_samples((a[0], a[1]), a[2] or (a[0], a[1]), b[3] or (b[0], b[1]), (b[0], b[1])):
                    ap(xf, *q)

    def walk(el, parent):
        for sh in el.findall("Shape"):
            xf = _xf_mul(parent, _xf_of(sh))
            t_ = sh.get("Type", "")
            if t_ == "Group":
                ch = sh.find("Children")
                if ch is not None:
                    walk(ch, xf)
            elif t_ == "Text":
                bp = sh.find("BackupPath")
                if bp is not None:
                    path_points(bp, _xf_mul(parent, _xf_of(bp)))
                else:
                    notes.append("Text ohne Kurvenkopie")
            elif t_ in ("Rect", "Bitmap"):
                w, h = float(sh.get("W", 0)), float(sh.get("H", 0))
                for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
                    ap(xf, x, y)
            elif t_ == "Ellipse":
                rx, ry = float(sh.get("Rx", 0)), float(sh.get("Ry", 0))
                for k in range(36):
                    th = k * math.pi / 18
                    ap(xf, rx * math.cos(th), ry * math.sin(th))
            else:
                path_points(sh, xf)

    walk(root, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
    return (bbox(pts) if pts else None), notes


# ---------------------------------------------------------------------------
#  Vorschau: Geometrie aufbereiten
# ---------------------------------------------------------------------------


def contour_polyline(c):
    """Kontur als Punktfolge (Beziers angenaehert)."""
    if not c.v:
        return []
    if not c.prims:
        return [(v.x, v.y) for v in c.v]
    first = c.prims[0][1]
    pts = [(c.v[first].x, c.v[first].y)]
    for t_, i, j in c.prims:
        a, b = c.v[i], c.v[j]
        if t_ == "B":
            pts.extend(bezier_samples((a.x, a.y), a.c0 or (a.x, a.y), b.c1 or (b.x, b.y), (b.x, b.y), 10))
        pts.append((b.x, b.y))
    return pts


class _FlatPen(object):
    """Glyphen-Umrisse aus fontTools als Polylinien (Fonteinheiten)."""

    def __init__(self, glyphset):
        from fontTools.pens.basePen import BasePen

        polys = []

        class P(BasePen):
            def _moveTo(self, pt):
                polys.append([pt])

            def _lineTo(self, pt):
                polys[-1].append(pt)

            def _curveToOne(self, p1, p2, p3):
                p0 = polys[-1][-1]
                polys[-1].extend(bezier_samples(p0, p1, p2, p3, 8))
                polys[-1].append(p3)

            def _qCurveToOne(self, p1, p2):
                p0 = polys[-1][-1]
                for k in range(1, 6):
                    t = k / 6.0
                    u = 1 - t
                    polys[-1].append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                                      u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
                polys[-1].append(p2)

            def _closePath(self):
                if polys and polys[-1]:
                    polys[-1].append(polys[-1][0])

        self.polys = polys
        self.pen = P(glyphset)


def text_polylines(d):
    """Nachbildung des LightBurn-Textsatzes (Av=1, Ausrichtung an der Glyphenkante)."""
    fm = d.get("fm")
    if fm is None:
        return []
    cache = fm.__dict__.setdefault("_polycache", {})
    H = d["H"]
    u = H / float(fm.upm)
    lines = d["text"].split("\n")
    n = len(lines)
    p = d["k"] * H * (1 + d["lns"] / 100.0)
    a, b, c, dd, tx, ty = d["xform"]
    out = []
    for i, line in enumerate(lines):
        base = ((n - 1) / 2.0 - i - 0.5) * p
        x = 0.0
        gl = []
        for ch in line:
            name = fm.cmap.get(ord(ch), ".notdef")
            if name not in cache:
                fp = _FlatPen(fm.gs)
                try:
                    fm.gs[name].draw(fp.pen)
                except Exception:
                    pass
                cache[name] = fp.polys
            for poly in cache[name]:
                gl.append([(x + px * u, base + py * u) for px, py in poly])
            try:
                adv = fm.hmtx[name][0]
            except KeyError:
                adv = fm.upm // 2
            x += adv * u + d["ls"] * H / 100.0
        xs = [q[0] for poly in gl for q in poly]
        if not xs:
            continue
        x0, x1 = min(xs), max(xs)
        sh = -x0 if d["ah"] == 0 else (-x1 if d["ah"] == 2 else -(x0 + x1) / 2.0)
        for poly in gl:
            out.append([(a * (q[0] + sh) + c * q[1] + tx, b * (q[0] + sh) + dd * q[1] + ty) for q in poly])
    return out


def build_preview(conv, ezd):
    """Zeichendaten fuer die Vorschau: Quelle, Ziel, Schraffur, Rahmen."""
    W = conv.W
    src, hatch_lines = [], []

    def rec(objs):
        for o in objs:
            if getattr(o, "hidden", False):
                continue
            if o.kind == "hatch":
                if o.cache is not None:
                    for cc in object_contours(o.cache, ezd):
                        hatch_lines.append([W(q) for q in contour_polyline(cc)])
                for cc in object_contours(o, ezd):
                    src.append([W(q) for q in contour_polyline(cc)])
                rec([ch for ch in o.children if ch.kind in ("group", "vectorfile", "combine", "hatch")])
            elif o.kind in ("group", "vectorfile", "combine"):
                rec(o.children)
            elif o.kind != "text":
                for cc in object_contours(o, ezd):
                    src.append([W(q) for q in contour_polyline(cc)])

    rec(ezd.objects)
    dst = list(conv.pv_paths)
    missing = []
    for d in conv.debug_texts:
        polys = text_polylines(d)
        if not polys:
            missing.append(d)
        dst.extend(polys)
        if d.get("hatch") is None:
            src.extend(polys)  # ungeschrafter Text: EZD speichert keine Umrisse
    boxes = [("feld", (0.0, 0.0, conv.field, conv.field))]
    if conv.size_mode == "vorlage":
        boxes.append(("vorlage", conv.tpl_outer))
        if conv.tpl_box != conv.tpl_outer:
            boxes.append(("innen", conv.tpl_box))
    ext = bbox([q for poly in dst for q in poly]) if dst else None
    return {"src": src, "hatch": hatch_lines, "dst": dst, "boxes": boxes, "missing": missing,
            "extent": ext, "scale": conv.s, "field": conv.field,
            "unhatched": sum(1 for d in conv.debug_texts if d.get("hatch") is None)}


# ---------------------------------------------------------------------------
#  LightBurn-Ausgabe
# ---------------------------------------------------------------------------


def fmt(x):
    if abs(x) < 5e-8:
        x = 0.0
    s = "%.6f" % x
    s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class Layer:
    def __init__(self, key, pen, mode, hatch=None):
        self.key = key
        self.pen = pen  # Pen oder None
        self.mode = mode  # "Cut" oder "Scan"
        self.hatch = hatch
        self.index = None


class Converter:
    def __init__(self, field_size=110.0, match_spacing=True, template=None,
                 font_map=None, log=print, fit=False, margin=1.0, scale_pct=100.0,
                 size_from_template=False, tpl_margin=0.0):
        self.field = float(field_size)
        self.off = self.field / 2.0
        self.fit = fit
        self.margin = max(0.0, float(margin))
        self.manual_scale = float(scale_pct) / 100.0 if scale_pct else 1.0
        self.s = 1.0
        self.tx = self.ty = self.off
        self.size_from_template = size_from_template
        self.tpl_margin = max(0.0, float(tpl_margin or 0))
        self.match_spacing = match_spacing
        self.template = template
        self.font_map = font_map or {}
        self.log = log

    # ---- Einstieg ----
    def W(self, p):
        """EZCAD-Koordinate -> LightBurn: Skalierung um die EZCAD-Mitte, Mitte auf Feldmitte."""
        return (p[0] * self.s + self.tx, p[1] * self.s + self.ty)

    def _reset(self):
        self.layers = {}
        self.layer_order = []
        self.shapes = []
        self.stats = {"text": 0, "path": 0}
        self.debug_texts = []
        self.extent = []  # in EZCAD-Koordinaten
        self.pv_paths = []  # Zielpfade fuer die Vorschau

    def convert(self, src, dst=None, preview=False):
        ezd = EzdFile(src)
        self.ezd = ezd
        self.s = 1.0
        self.tx = self.ty = self.off
        self.size_mode = None
        self._reset()
        self._walk(ezd.objects, self.shapes)
        self.orig_extent = bbox(self.extent) if self.extent else None
        tb = None
        if self.size_from_template and self.template and self.orig_extent:
            try:
                tb, tnotes = template_extent(self.template)
                if tnotes:
                    ezd.warn("Vorlage enthaelt Texte ohne Kurvenkopie (nie in LightBurn gespeichert) - "
                             "Groesse aus Vorlage nur aus den uebrigen Objekten bestimmt")
            except Exception as ex:
                ezd.warn("Groesse aus Vorlage nicht lesbar: %s" % ex)
                tb = None
            if not tb:
                ezd.warn("Vorlage hat keinen auswertbaren Inhalt - Groesse nicht uebernommen")
        self.tpl_outer = tb
        if tb and self.tpl_margin > 0:
            m = self.tpl_margin
            if tb[2] - tb[0] > 2 * m and tb[3] - tb[1] > 2 * m:
                tb = (tb[0] + m, tb[1] + m, tb[2] - m, tb[3] - m)
            else:
                ezd.warn("Rand %s mm ist groesser als der halbe Vorlagen-Kasten - ohne Rand eingepasst" % fmt(m))
        if tb:
            x0, y0, x1, y1 = self.orig_extent
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            cw, ch = x1 - x0, y1 - y0
            cand = [r for r in ((tw / cw) if cw > 1e-9 else None, (th / ch) if ch > 1e-9 else None) if r]
            self.s = min(cand) if cand else 1.0
            self.tx = (tb[0] + tb[2]) / 2.0 - self.s * (x0 + x1) / 2.0
            self.ty = (tb[1] + tb[3]) / 2.0 - self.s * (y0 + y1) / 2.0
            self.size_mode = "vorlage"
            self.tpl_box = tb
        elif self.fit and self.orig_extent:

            x0, y0, x1, y1 = self.orig_extent
            half = self.field / 2.0 - self.margin
            need_x = max(abs(x0), abs(x1))
            need_y = max(abs(y0), abs(y1))
            s = min(half / need_x if need_x > 1e-9 else 1e9, half / need_y if need_y > 1e-9 else 1e9)
            self.s = s if 0 < s < 1e8 else 1.0
        else:
            self.s = self.manual_scale
        if abs(self.s - 1.0) > 1e-9 or self.size_mode:
            self._reset()
            self._walk(ezd.objects, self.shapes)
        if self.extent:
            x0, y0, x1, y1 = bbox([self.W(p) for p in self.extent])
            if x0 < -0.01 or y0 < -0.01 or x1 > self.field + 0.01 or y1 > self.field + 0.01:
                ezd.warn("Design (%.1f x %.1f mm) ragt ueber das Galvo-Feld von %s mm hinaus - Feldgroesse pruefen "
                         "oder 'Auf Feld einpassen' nutzen" % (x1 - x0, y1 - y0, fmt(self.field)))
        self._assign_layer_indices()
        xml = self._build_xml(src)
        if dst:
            with open(dst, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(xml)
        self.stats["scale"] = self.s
        self.preview = build_preview(self, ezd) if preview else None
        return ezd, self.stats

    # ---- Layer ----
    def _layer(self, pen_idx, mode, hatch=None):
        pen = self.ezd.pen(pen_idx)
        if mode == "Scan" and hatch is not None:
            key = (pen_idx, mode, round(hatch.h1_space or 0, 5), round(hatch.h1_angle or 0, 3),
                   hatch.crosshatch)
        else:
            key = (pen_idx, mode)
        if key not in self.layers:
            self.layers[key] = Layer(key, pen, mode, hatch)
            self.layer_order.append(key)
        return self.layers[key]

    def _assign_layer_indices(self):
        used = set()
        # zuerst: Layer-Nr. = Stift-Nr. (erste Variante je Stift)
        for key in self.layer_order:
            L = self.layers[key]
            p = key[0]
            if p < 30 and p not in used:
                L.index = p
                used.add(p)
        free = [i for i in range(30) if i not in used]
        for key in self.layer_order:
            L = self.layers[key]
            if L.index is None:
                if not free:
                    raise EzdError("Mehr als 30 Layer noetig - LightBurn unterstuetzt nur 30")
                L.index = free.pop(0)

    # ---- Baum durchlaufen ----
    def _walk(self, objs, out):
        i = 0
        while i < len(objs):
            o = objs[i]
            nxt = objs[i + 1] if i + 1 < len(objs) else None
            if o.hidden:
                self.ezd.warn("Ausgeblendete EZCAD-Objekte wurden nicht uebernommen")
                i += 1
                continue
            if (o.array_x or 1) > 1 or (o.array_y or 1) > 1:
                self.ezd.warn("EZCAD-Arrays wurden nur als Einzelobjekt uebernommen")
            if o.kind == "text":
                hatch = None
                if nxt is not None and nxt.kind == "hatch" and not self._has_text(nxt) and \
                        _dist(nxt.position, o.position) < 1e-3:
                    hatch = nxt
                    i += 1
                self._emit_text(o, hatch, out)
            elif o.kind == "hatch":
                self._emit_hatch(o, out)
            elif o.kind == "image":
                self.ezd.warn("Bitmaps werden nicht unterstuetzt und wurden weggelassen")
            elif o.kind == "control":
                self.ezd.warn("Steuerobjekte (Timer, Ein-/Ausgaenge, Achsen) wurden weggelassen")
            elif o.kind in ("group", "vectorfile", "combine") and self._has_text(o):
                sub = []
                self._walk(o.children, sub)
                out.append(self._group(sub))
            else:
                cs = object_contours(o, self.ezd)
                if cs:
                    L = self._layer(o.pen, "Cut")
                    out.append(self._path(cs, L))
            i += 1

    def _has_text(self, o):
        if o.kind == "text":
            return True
        return any(self._has_text(c) for c in getattr(o, "children", []))

    def _emit_hatch(self, h, out):
        pen = h.h1_pen if (h.supported and h.h1_on) else h.pen
        if not h.supported:
            self.ezd.warn("Aelteres Schraffurformat: Linienabstand/Winkel als Standard (0.05 mm / 0 Grad) gesetzt")
        if self._has_text(h):
            # Schraffur, die Textobjekte direkt enthaelt
            sub = []
            for ch in h.children:
                if ch.kind == "text":
                    self._emit_text(ch, h, sub)
                else:
                    cs = object_contours(ch, self.ezd)
                    if cs:
                        sub.append(self._path(cs, self._layer(pen, "Scan", h)))
            if len(sub) > 1:
                out.append(self._group(sub))
            elif sub:
                out.append(sub[0])
            return
        cs = []
        for ch in h.children:
            cs.extend(object_contours(ch, self.ezd))
        if not cs:
            return
        out.append(self._path(cs, self._layer(pen, "Scan", h)))
        if h.mark_contours:
            out.append(self._path(cs, self._layer(h.pen, "Cut")))

    # ---- Text ----
    def _emit_text(self, t, hatch, out):
        ezd = self.ezd
        text = t.text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        n = len(lines)
        m = t.matrix or (1, 0, 0, 0, 1, 0, 0, 0, 1)
        a, b, c, d, e, f_ = m[0], m[1], m[3], m[4], m[6], m[7]
        sy = math.hypot(c, d) or 1.0
        H = t.height * sy

        font = self.font_map.get(t.font.lower(), t.font)
        fm = None
        try:
            fm = FontMetrics.lookup(font, t.bold)
        except Exception:
            fm = None
        if fm is None:
            ezd.warn("Schrift '%s' nicht gefunden (oder fontTools fehlt): Metriken von Arial verwendet" % font)
        k = fm.k if fm else ARIAL_K
        # EZCAD (GDI): Zeichenbreite / mittlere Zeichenbreite der Schrift = horizontale Skalierung
        avg = fm.avg if (fm and fm.avg) else ARIAL_AVG[t.bold]
        sx = 1.0
        if t.width and t.height and avg:
            sx = (t.width / t.height) / avg
            if not (0.2 < sx < 5.0) or abs(sx - 1.0) < 1e-3:
                sx = 1.0

        # Rahmen U (LightBurn-Text lokal): Welt = X*u + T, X = (M/sy) * diag(sx, 1)
        Xa, Xb, Xc, Xd = a / sy * sx, b / sy * sx, c / sy, d / sy
        det = Xa * Xd - Xc * Xb
        if abs(det) < 1e-12:
            det = 1.0

        def to_U(p):
            x, y = p[0] - e, p[1] - f_
            return ((Xd * x - Xc * y) / det, (-Xb * x + Xa * y) / det)

        def from_U(u):
            return (Xa * u[0] + Xc * u[1] + e, Xb * u[0] + Xd * u[1] + f_)

        # Glyphen-Konturen aus der Schraffur (exakte Lage der Buchstaben)
        glyph_groups = None
        all_pts = []
        if hatch is not None:
            groups = []
            for ch in hatch.children:
                if ch.kind == "group" and ch.children and all(g.kind in ("group", "curve", "combine") for g in ch.children):
                    groups.extend(ch.children)
                else:
                    groups.append(ch)
            gpts = []
            for g in groups:
                pts = [to_U(p) for cc in object_contours(g, ezd) for p in cc.sample()]
                gpts.append(pts)
                all_pts.extend(pts)
            nonspace = [ch for ch in text if not ch.isspace()]
            if len(gpts) == len(nonspace):
                glyph_groups = gpts
            elif text.strip() and not any(ch.kind == "group" for ch in hatch.children):
                ezd.warn("Textobjekt '%s' ist kein normaler Text (z.B. Barcode) - als Kontur uebernommen" % text[:30])
                self._emit_hatch(hatch, out)
                return

        # Zeilenabstand: EZCAD-Zeilenabstand = (Hoehe + Zeilenluecke) * Skalierung
        pitch = (t.height + t.line_gap) * sy
        if n > 1:
            lns = (pitch / (k * H) - 1.0) * 100.0
            p_lb = pitch
        else:
            lns = 0.0
            p_lb = k * H
        b_last = t.ref[1] * sy  # Grundlinie der letzten Zeile (EZCAD-Bezugspunkt)
        ay = b_last + n * p_lb / 2.0  # Bezugspunkt fuer LightBurn Av=1

        # horizontale Ausrichtung (LightBurn richtet an der sichtbaren Glyphenkante aus)
        ah = t.align if t.align in (0, 1, 2) else 0
        if all_pts:
            bx0, _by0, bx1, _by1 = bbox(all_pts)
            ax = bx0 if ah == 0 else (bx1 if ah == 2 else (bx0 + bx1) / 2.0)
        else:
            ax = t.ref[0] * sy / sx

        # Rest-Zeichenabstand an EZCAD angleichen (HSpace, % der Schrifthoehe)
        ls = 0.0
        if self.match_spacing and fm is not None and glyph_groups is not None:
            vals = []
            gi = 0
            for line in lines:
                cnt = len([ch for ch in line if not ch.isspace()])
                pts = [p for g in glyph_groups[gi:gi + cnt] for p in g]
                gi += cnt
                st = line.strip()
                if len(st) < 4 or not pts:
                    continue
                bb = bbox(pts)
                w_lb = fm.ink_width(st, H)
                if w_lb:
                    vals.append(((bb[2] - bb[0]) - w_lb) / (len(st) - 1) / H * 100.0)
            if vals:
                vals.sort()
                ls = vals[len(vals) // 2]
                if abs(ls) < 0.75:
                    ls = 0.0
                ls = max(-50.0, min(100.0, ls))

        if t.variable:
            ezd.warn("Variabler Text (Seriennummer/Datum/Datei) wurde als fester Text '%s' uebernommen" % text[:30])

        wx, wy = self.W(from_U((ax, ay)))
        xa, xb, xc, xd = Xa, Xb, Xc, Xd
        if abs(xa - 1) < 1e-3 and abs(xd - 1) < 1e-3 and abs(xb) < 1e-6 and abs(xc) < 1e-6:
            xa, xb, xc, xd = 1.0, 0.0, 0.0, 1.0

        if hatch is not None:
            pen = hatch.h1_pen if (hatch.supported and hatch.h1_on) else hatch.pen
            L = self._layer(pen, "Scan", hatch)
        else:
            L = self._layer(t.pen, "Cut")
        weight = 700 if t.bold else 400
        font_str = "%s,-1,4096,5,%d,0,0,0,0,0" % (font, weight)
        attrs = [
            ("Type", "Text"), ("CutIndex", L), ("Font", font_str), ("Str", text),
            ("H", fmt(H * self.s)), ("LS", fmt(ls)), ("LnS", fmt(lns)), ("Ah", str(ah)), ("Av", "1"),
        ]
        if t.bold:
            attrs.append(("Bold", "1"))
        attrs += [("Weld", "1" if hatch is not None else "0"), ("HasBackupPath", "0")]
        parts = []
        for k_, v in attrs:
            if isinstance(v, Layer):
                parts.append("%s={L:%s}" % (k_, id(v)))
            else:
                parts.append("%s=%s" % (k_, quoteattr(v).replace("\n", "&#10;")))
        xml = ("<Shape " + " ".join(parts) + ">\n"
               "    <XForm>%s %s %s %s %s %s</XForm>\n</Shape>"
               % (fmt(xa), fmt(xb), fmt(xc), fmt(xd), fmt(wx), fmt(wy)))
        out.append((xml, [L]))
        self.stats["text"] += 1
        for p in all_pts:
            q = from_U(p)
            self.extent.append(q)
        self.debug_texts.append(dict(text=text, H=H * self.s, ls=ls, lns=lns, ah=ah, bold=t.bold, k=k,
                                     xform=(xa, xb, xc, xd, wx, wy), hatch=hatch, sx=sx, fm=fm))
        if hatch is not None and hatch.mark_contours:
            cl = self._layer(t.pen, "Cut")
            out.append((xml.replace("{L:%s}" % id(L), "{L:%s}" % id(cl)), [cl]))

    # ---- Pfade ----
    def _path(self, contours, L):
        verts = []
        prims = []
        base = 0
        W = self.W
        for c in contours:
            self.extent.extend(c.sample())
            self.pv_paths.append([W(q) for q in contour_polyline(c)])
            for v in c.v:
                px, py = W((v.x, v.y))
                s = "V%s %s" % (fmt(px), fmt(py))
                if v.c0:
                    q = W(v.c0)
                    s += "c0x%sc0y%s" % (fmt(q[0]), fmt(q[1]))
                else:
                    s += "c0x1"
                if v.c1:
                    q = W(v.c1)
                    s += "c1x%sc1y%s" % (fmt(q[0]), fmt(q[1]))
                else:
                    s += "c1x1"
                verts.append(s)
            for t_, i, j in c.prims:
                prims.append("%s%d %d" % (t_, i + base, j + base))
            base += len(c.v)
        self.stats["path"] += 1
        xml = ('<Shape Type="Path" CutIndex={L:%s}>\n    <XForm>1 0 0 1 0 0</XForm>\n'
               '    <VertList>%s</VertList>\n    <PrimList>%s</PrimList>\n</Shape>'
               % (id(L), "".join(verts), "".join(prims)))
        return (xml, [L])

    def _group(self, items):
        items = [it for it in items if it]
        layers = [L for _x, ls in items for L in ls]
        inner = "\n".join(x for x, _ in items)
        inner = "\n".join("        " + ln for ln in inner.split("\n"))
        xml = ('<Shape Type="Group">\n    <XForm>1 0 0 1 0 0</XForm>\n    <Children>\n%s\n    </Children>\n</Shape>'
               % inner)
        return (xml, layers)

    # ---- Datei ----
    def _template_parts(self):
        if not self.template:
            return None
        try:
            root = ET.parse(self.template).getroot()
        except Exception as ex:
            self.ezd.warn("Vorlage nicht lesbar: %s" % ex)
            return None
        return root

    def _cut_setting(self, L, tpl_root):
        pen = L.pen
        lines = ['    <CutSetting type="%s">' % L.mode]
        name = "Pen %d" % L.key[0]
        if pen and pen.name:
            name += " " + pen.name
        if L.mode == "Scan":
            name += " Fill %s°" % fmt(L.hatch.h1_angle or 0) if L.hatch else " Fill"
            if L.hatch and L.hatch.crosshatch:
                name += " Kreuz"
        else:
            name += " Linie"
        vals = [("index", str(L.index)), ("name", name)]
        tpl = None
        if tpl_root is not None:
            for cs in tpl_root.findall("CutSetting"):
                ie = cs.find("index")
                if ie is not None and ie.get("Value") == str(L.key[0]):
                    tpl = cs
                    break
        skip = {"index", "name", "interval", "angle", "crossHatch", "priority"}
        if tpl is not None:
            for ch in tpl:
                if ch.tag not in skip and ch.get("Value") is not None:
                    vals.append((ch.tag, ch.get("Value")))
        elif pen is not None:
            vals += [("maxPower", fmt(pen.power)), ("speed", fmt(pen.speed)),
                     ("frequency", str(int(pen.frequency or 0)))]
            if pen.pulse_width:
                vals.append(("QPulseWidth", fmt(pen.pulse_width)))
            if pen.start_tc is not None:
                vals.append(("LaserOn_TC", fmt(pen.start_tc)))
            if pen.end_tc is not None:
                vals.append(("End_TC", fmt(pen.end_tc)))
            if pen.jump_speed:
                vals.append(("JumpSpeed", fmt(pen.jump_speed)))
            vals.append(("OverrideGalvoTimings", "1"))
            vals.append(("numPasses", str(max(1, int(pen.passes or 1)))))
        if L.mode == "Scan" and L.hatch is not None:
            vals += [("interval", fmt(L.hatch.h1_space or 0.05)),
                     ("angle", fmt(L.hatch.h1_angle or 0)),
                     ("crossHatch", "1" if L.hatch.crosshatch else "0")]
        vals.append(("priority", str(L.index)))
        for k_, v in vals:
            lines.append("        <%s Value=%s/>" % (k_, quoteattr(v)))
        lines.append("    </CutSetting>")
        return "\n".join(lines)

    def _build_xml(self, src):
        tpl = self._template_parts()
        root_attrs = [("AppVersion", "2.2.01"), ("DeviceName", "JCZFiber (Unknown)"), ("FormatVersion", "1"),
                      ("MaterialHeight", "0"), ("MirrorX", "False"), ("MirrorY", "False")]
        extra_blocks = []
        if tpl is not None:
            root_attrs = [(k_, v) for k_, v in tpl.attrib.items() if k_ not in ("CurrentPage",) and not k_.startswith("Page")]
            for tag in ("VariableText", "UIPrefs", "GalvoRotaryConfig"):
                el = tpl.find(tag)
                if el is not None:
                    ET.indent(el, space="    ", level=1) if hasattr(ET, "indent") else None
                    extra_blocks.append("    " + ET.tostring(el, encoding="unicode").strip())
        out = ['<?xml version="1.0" encoding="UTF-8"?>']
        out.append("<LightBurnProject " + " ".join("%s=%s" % (k_, quoteattr(v)) for k_, v in root_attrs) + ">")
        out.extend(extra_blocks)
        for key in sorted(self.layer_order, key=lambda k_: self.layers[k_].index):
            out.append(self._cut_setting(self.layers[key], tpl))
        layer_by_id = {"{L:%s}" % id(L): L for L in self.layers.values()}
        for xml, _ls in self.shapes:
            for tok, L in layer_by_id.items():
                xml = xml.replace(tok, '"%d"' % L.index)
            out.append("\n".join("    " + ln for ln in xml.split("\n")))
        if self.size_mode == "vorlage":
            note = ("Konvertiert aus EZCAD2-Datei %s mit ezd2lbrn %s. Laserparameter vor dem ersten Lauf "
                    "an Testmaterial pruefen." % (os.path.basename(src), VERSION))
        else:
            note = ("Konvertiert aus EZCAD2-Datei %s mit ezd2lbrn %s. EZCAD-Mitte = %s/%s (Feld %s mm). "
                    "Laserparameter vor dem ersten Lauf an Testmaterial pruefen."
                    % (os.path.basename(src), VERSION, fmt(self.off), fmt(self.off), fmt(self.field)))
        if self.size_mode == "vorlage":
            note += (" Groesse/Lage aus Vorlage %s uebernommen (Rand %s mm): Kasten innen %.1f x %.1f mm, "
                     "Mitte %.2f/%.2f." % (os.path.basename(self.template), fmt(self.tpl_margin),
                                           self.tpl_box[2] - self.tpl_box[0],
                        self.tpl_box[3] - self.tpl_box[1], (self.tpl_box[0] + self.tpl_box[2]) / 2,
                        (self.tpl_box[1] + self.tpl_box[3]) / 2))
        if abs(self.s - 1.0) > 1e-9:
            note += (" Design skaliert auf %s %% (Texthoehen, Abstaende und Konturen; Schraffur-Linienabstand "
                     "unveraendert)." % fmt(round(self.s * 100, 2)))
        if self.ezd.warnings:
            note += " Hinweise: " + " | ".join(self.ezd.warnings)
        out.append('    <Notes ShowOnLoad="%d" Notes=%s/>' % (1 if self.ezd.warnings else 0, quoteattr(note)))
        out.append("</LightBurnProject>")
        return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
#  Oberflaeche
# ---------------------------------------------------------------------------


# Vollstaendige Lizenztexte der Drittkomponenten (zlib-komprimiert, base64)
LICENSES_Z_B64 = (
    "eNrtvflyG0eWN/p/PkUOI74r0lOERMqyLXm+uQ2RkAQ3tyEpy3JHx6gAJIBqFarYVQVSdNwXmteYJ7tny6UWkCBFd/dMU+Fm"
    "k0BV7nn28zsHw18HR/Cf+r/yT7nfvu6fMr9NdtNRkeltfXxhMn2WL4ux0cusMoWewP8Oh+fbB8lvJvtNzavqonz19GmSV9tl"
    "srhIr3vjfPFQI1HQkz5IxiYrjVJ7+cV1kczmld4cb+ndZ7vf6b15kZRVfjGHUZ2N54tk/DnSjbGoE1MskrJM8kwnpYZHzeha"
    "z4oY5jOJ9LQwRudTPZ7HxcxEusp1nF3rC1OU8EI+quIkS7KZjvUY+lfwZDWHZsp8Wl3FhYGHJzouy3ycxNCenuTj5cJkVVxh"
    "f9MkNaXerOZGb5zJGxtb1MnExKlKMo3f2a/0VVLN82WlC1NWRTLGNmA+2ThdTnAM9us0WSTSA75Oi1IqaHRZwgxwnJFe5JNk"
    "iv9vaFoXy1GalPNITxJserSs4MMSP6TVjXAeT/NClyZNFbSQwLhprn509AwO/QIXtJIlKvGTq3m+qM8kKdV0WWTQpaF3Jjks"
    "GfX4FzOu8BN8fJqnaX6FUxvn2STBGZWvlDqHr+JRfmloLrznWV7BUHkIuAEXflflq3Iep6keGVkw6BeWNw6mU2D3ZQUbn8Sp"
    "vsgL6q85zR70/26gz47fnH/onw708EyfnB7/PNwf7OuN/hn8vRHpD8Pzd8fvzzU8cdo/Ov+oj9/o/tFH/cfh0X6kB7+cnA7O"
    "zvTxqRoenhwMB/DZ8Gjv4P3+8Oitfg3vHR3DwR7C8YZGz481dihNDQdn2Njh4HTvHfzZfz08GJ5/jNSb4fkRtvnm+FT39Un/"
    "9Hy49/6gf6pP3p+eHJ8NoPt9aPZoePTmFHoZHA6OznvQK3ymBz/DH/rsXf/gALtS/fcw+lMcn947Pvl4Onz77ly/Oz7YH8CH"
    "rwcwsv7rgwF3BZPaO+gPDyO93z/svx3QW8fQyqnCx3h0+sO7AX6E/fXhv73z4fERTmPv+Oj8FP6MYJan5+7VD8OzQaT7p8Mz"
    "XJA3p8eHkcLlhDeOqRF472jAreBS69qOwCP49/uzgWtQ7w/6B9DWGb6MU7QPw27+cVn8puPlVO+bZVWO56/0TwbJ2CQuptoR"
    "u895WZkszUudLSugbZG+NEVsMngyA9o3UVcmqfAujfC7eDme4/EyycxkQCNPivwv5jM00NOvDd7VZTZ7BR0WQEHgEe1o1za1"
    "xdRznmTQaKlHqUlGlTYFHGBqYlDqGX7y2SSZ0W/NVWzmBTxUVtAskOUkK0cG7h6MzaiDuDTFRVzEC4MEGprXJ3lJdwkGdpkj"
    "zV5A4zg9fYDrEGf6HAjMAghWQTehWJop9KsOgZDBBIEmwOTKioi9yWZAOMZwQXjQlflSwaLC5jP/0T/DHu2fDs/P/3h8eHJ8"
    "BCctYEor6Pp+Qnd/hsubScPwy8zg/PV0Cf2eG6CdEe3bhJbY7RR8NIqBCo5+u+oBOTNIRXGkPfPFwHArXkVdJtmkp8+gJ1Ok"
    "8RK2DR/WP5viCrrFhXT7V3gap8o8jTPYMXi2NPodb5HRQPBmhjZfX5kCR70NBwj2ycDaJAYIFPwsdLLAowDMZbGARTqChaz8"
    "kF4hjyqSaTWBlU+giU352zeaLQvYMp6PPVsfYCL5VbmthhkQrzRlwg9rBQPMtoCsXiV4wCaG3hz8utff1zk2cIAH7jVQYb2J"
    "JyXT+9jvNC9w51XQo7TFK0ozKhI+58sFvYSLXv62rOAnnIRsCw/AA/H4nS19aEzxx2+fVbCkbws4v2mM6w8Ue/Dr/vYBjAxG"
    "nZZKa23FjRkwwuUI2fvTBbz8GV52v/yNhI8d7Tp8lDAeJYxHCeOfWMJ4KJKzuwXnNavO8zwtgRgKdwC+XiSfDUkh+m16DdpO"
    "toIY4tsVvu1/+9uQw53v9U9LkBguQbY4zcty+ah3PVLFR6r4z00Vt7e3PT175cV8JxCDjPqmMIsJyndGw+NKvXH0T05T2Tiw"
    "2CBoLDke5xL+KnQFlxa/uVgWF3lpygiuBWowcKbUEnUkfTY8YFMWtm6JWISqAJ3jnd4OHP6DHM6oKYCytojbs0ifxKM018PF"
    "BQrgRaI3r66ueon9E4nv/+f++sNsEScpfrYVQXNIPfQpiLHFJdwNGsIRKGtaOuzBI+d4s+ib4CJroRETzbOgy3LrTFDtyOGa"
    "4wPlgzf9y/D8rLYFHcv1bGcb10wDtad2z4f6ZDmCx2B9sgT3B4hOmfCmCRHoA7lMxjHyNdRykgroJH53Mr8ukzG8Ej6k9+Zm"
    "Ab+kMKVxYqrrqNnGYQwfgJaz+iFpOfx6kJbmEpQ4oBnZuMdUFyhic0iDFKZd0Kv4BP+ZZ8lYD7IZ6OuwXtwCnxF48wsuWNnL"
    "i9mN5wEW6hfetij4XW+eH27hlsVaV0UMunxcfMZx3GVoZXNsCl49sYR4XNvAnZcvf4ANfPYcN/AwGRf5CbBF+57mWS3w8wv8"
    "fBvuKR/1lRM7P6QN6enzXBg7NBNPmO/AWBfhbk15AS5SE5fIh4ClA99qjiPS3/2w/fyZfhcXRX4FzZ1VIE9UEfSKPBz09jSF"
    "R4GI7uw8//5FBESrD5d4nmfmld78fueHLf3i+xfbOz/sfHfLWtA5rh8N9eDXagh06/Jz3EV7XsBe7D7Tr0E+MMCYh9kl9LM5"
    "4j//UF1fmHzay0y19fB0hFqTl7Ed4ucT4PRAivl64LFMS5DcLoHgAYVkSQqO6pv+f+i4ekX+ARBRy3GRXMAVKIEswjV4evzm"
    "gNjDff8pGv/J4Ah45RFy9L3BEXClYPggPu9+p9+YUbGMi2ukSt9/VYcKJIv+IXBnEpJmOUzb0q7WOupNmN8W8h+Ut+B+LpYp"
    "CKn6Ki/SyVUyMWoCe5fmFyiwkgwM3A3krgKuwKWpcziSWcvlBUpNwgmzSo0Lw2KomQIHrGgo8RiJA5Ah3JgUTZBIYsfQ+GKx"
    "zOCuIeVlSVJB65cwDtgoEsPx0xxnMUUrIgzzM4pwzEXpPsIVvUYJD0S9Ak4APK+A20EjLOxdxEUFzLycJxd8AHIYaVH2WKKE"
    "xUBxML9iVu5OI7cM04OGQYAGnaCsQN7GX0iITqQn6NHJzhMacHoNor9Oc1QPqNFrWmyQR0HYTSfCeRYgU1+askeUUsiKl+lR"
    "2YDbkFzyouOkkTMBZ4DhjNAKhQMxi5GZgEgbNUZhpXbsjY88NOd0E1lelJjjir4qhCqqDBa4dKPFaeNomwOhMcsC4eb478tI"
    "zfMrOD4FDRYbgfEWhsilveHYI22BRvqAh0MWndeiMH9dJiB34elDAcptBHwGxNmRieD2T3IYNXYWX1zA6sMRovUTpUvTecTu"
    "S1zayo09J2qTFOEE4FTsD94Mj4YoZZ6pjRq52oAxTOHoWEWlNHRBWJ9zsxTJwtFK9Q72wRSb5VbX2HEBx/BmAQNH5onbB4rk"
    "cowcCR+Ew61E2oQ9JGcndRjBOUhgg4V68U6EeiZMZaPN78I54CrxlpcXZixnmjvX8bRiaqw80wEtqaKNgblg68fwcYI8Ukhb"
    "c32QchjSV3GZaksJrS4u0AeAIy91eHqb66fd+mGfh/bydfTZuDULIDnYGjJz3Pg8AtKWGpTsIqf3oXiC325vW1qBXxGByUns"
    "5/M6laVwg7bktbEEEXY4nsco0KA6gNZsOmnwMemW2Uzhe3U2iGPXmbkC5eMyAZkI1xgn21+Cal+0p1gmswzvmBG5KcJLTXZ9"
    "/L0y43mG4oq6KtArhN3zhRP7BUyNZBe74rJdtTFB9yeD08Ph2RmqW/8P6l77cikexHSi2HRi17HWN71EVgukudfOeMFGC6J6"
    "kbKmjJD0MQtBa0WNSMO9c3/WrBiq0e16hoidLX1kEqZgra3M8sKemKRCPXGSALVdwgnxhwcpvXInB15oHmp4wvI04RjQlkmn"
    "sCm7W/rGNzsX1LZmOcdd+EXUYBgmBupAVi8Sf5OMGedtlpmQxqPoTKxMOaOMrCdSnwre2Y6Bf8K9MF8qS+3my0WcbQMpn5Ao"
    "N4df8E7khbWXAfFHqR8tbjDf8Rxuhn98YSr4rQKRIjHppKRp4nvYATQxgvUEQYwpeY17g+xr3xH2C1Q+AWINEveVp1ZwWkFX"
    "Vs/haOStTVm9J/AafVOj0wrpNLMLGE6pzZcLWL2k0nid0d11UbuAcvNkJLArBfDzCzywMIcmFRWmEpgSdZ6hxAKcE+8Fn3wQ"
    "wZIFLQiOGFkELgZqVCYLCAZOGjnmt1skEWQybJltBwEXlquZrgXP0rTdyrDNTqQHkkLQvljkixyvuMkmeQHrhhdtAqJGlRAb"
    "vVbNZYdHv4zNBV3nePw5y6/g7M+MsCYhf/CcH4fqGjMf4dqgC74mLD00t0f57YG1ecFr0yA0jhzlRUCcoi4WBJQObdcjU2OS"
    "sA8gJMHGtUUKJoL0Ei+hCl9symDhvdSrZC91B9lLr5a9VBeXOUcuc9RH1lLX6UYGKCY2vkzZYnCZJyDQTwOG7KiOpc5kVsPR"
    "wH1HUW54RmbKwSlZb0kd+woTrvImXL2eCRdfrZlwVdOEq+9jwlVkRBUbbQQvncOHkT4/7e8PDvunf4y8CZQeWWXybdp51zfz"
    "am/mVX4F8IW3g6PBaf8g0mcng70h/gJLNzwdoLkXnoTlhskfRGxmBr34P97DB/Ccki6+zmysyGysxWwcGIThiPF4cVvws/aB"
    "gKfwbdVpO64/2xOvWqdJuD/J4bb23x6A2AQ/3xxZmy8cbr5Bz7fHaQyErdSvz/btgQfJ4tSzZaTMJLqgdSOzkj9+YnkVyJal"
    "mLWEIOXLisUiEP7YS0T6Hjlm8BLWGfkqCYfuENyf1nhItpCBjDGig2hMYVAIuEEGiJQQjFLMCb4joax+FNAdrEyyIGba7B16"
    "CeZue4dJTZZj0zUAJQPQ9xmA5kmpuu9ORCWmnTZGqfQra/mCDoeOVu9AYCR+CgPhc3J2XYJSVaLtLi8u0MwCzWS5f7QUWVI5"
    "ngXsz0p0ljsGTFEYpeaFAfpNKhEZJ/KFqvspre9Q9L8xvJTgYWoJGuT/AorZST1ff6Qb0vYaIemiWzsE2nh8Cvo001n6AolF"
    "m6AG5DKgrZElrsoT14g6bb/W4Sij/gJCq7oJLUzLcYz9FTTTz1IJzWRC5ubYQUM9+esghMrRycEvA5hM//Rjmzg6+rt5y5LA"
    "nuy9PyUmgetw9v712fnw/P35QL89Pt6nhT4bnP483Buc/agPjs9otYAcRkB+z/vUMTQBSwVfw++v3wNRxUUbHgGfPn1/grR4"
    "C/b3AywLjLEPr+7T6h4jsf6IbPb4lLhmtx/Q0/AzWLG98/Ax9OABYVd+jvpo8PZgCBxlb1Aj91vOS0g8AnnsxwbNV/RrcGIj"
    "2kk9BH6+//MQhy0Pw96fDeWc0JLtvZPlvpHKj5e7f10GlN2LjrvPdr7Tb/N8lhp2tfRBgDkld7wT9HvqoG337l/EHMrYMHjv"
    "9p5F2uvLrktH/MTs/Qpt/7f8q/dx0/O13n+KM2uq/nblS2JPRxdMTN2QOV3YW/mURofiniMMYlGgi4KHUJ8O4PTtvyeWH9FT"
    "cCH5aqF8iA3s9PS+mSYZk/CenfKGzGhDtIeFiZkpAYleMKUPCD8Kt2RgsEtI7FKaIktajQ8jDYUuWck6Y5NWqXeg/SJfzub6"
    "pbYRIJZbNMeFVpzGwDyfzK8yttKgVF+BMkaaRvIb9SftdL1BbNxqgNa2KesQDMDM4lQPqOnWIJZZoJ/GY2rFjgJjWNJUmmGO"
    "R1+RnohdE0PKUxYz5I+UBk32NfyUDzda+fNMWpIHLauMK+mwh+4xGof1lftVdRtu92hDWtmgqZR6M9niV/MrNIFNQIsZixrF"
    "v5NNaRyLwi2t8Fe0AsjNs3hmrNeDjKA8MLzkhqaPtj3sN6a2w5W5SvA0QSubCYyEtoccDmQenlZk/xpj05svnv0f0iAXeWGs"
    "2ikNLSsyfuAekC+jtC1CkyOTwSKMMUal1nowTr/lH/Plht6Ed/G3YmMr3HX4LzBKwTPh+ZAGzBcYbULmci8CNCwNsC2to8bJ"
    "OBssnzVO2gUZMQtyrcC3U1rxz9hFKLKWdoO9LwQuISmYFN/E0o6TXwJpNKpbv6UZfiCy93+azJZFEIXlh35Mlr/20FHZpM9g"
    "O5Yp3Q+Uo+BLtPKSlVUuKCxOyQZfOVD0iQREowvM5irhQ1F9gtJGY5poMUzwQuVsluRpUkx4O6wspF4w00trD4R2RFoFcTom"
    "n0sw7Q958blFFMjHhiMmOoQnzV+BxKVcuQvASyfTIqO797h65ua4WsxW0XEsRyl2dKFhO7TkLYzcQrJSVchbnBUKRytNbMIE"
    "zJd4cZGSS9hJ5aKf9C8uQE5OvjC33PKrsO+dBrgg5UbzBGAf3Wsgs5eWeA3swEfkD4Ld38yLmhTOtAq7ou3Cu8BM3RMD2CyQ"
    "8/G6F+YyEaMwudTknmhjvbL0l1dKwtskjSGXI9sdrX4shiUkBbm1Jbf3vE2PLZ2a1q5/pJvLFxjwrOeKHIe8MGxLsvfTYHII"
    "nhRcF5oG6F5o2EqT7DMtHOh8dE5QF9qym55gvt80HhOTiAIe6Ra1NShcHQxHcLu+F9gAO3e8eQfclQ36cwt4Wbf1unFgY7U9"
    "YcOlSCK2pZzXht6C71cNPgouRYVUH0NUUku2y+VINH1oz8oddLpo5GwwzvzwxHbdECvsLhO7u5FbhIIKBQJg93jeRwYWcwpL"
    "sVp4WY/b6w03pw1pi/m9I8vwknGRRJQWNYpTOkdWiZUQA159jbcgXHTjFwrXqSr9ZRGX9o2syNGusA8MffCBVxhuhy+j/QFa"
    "Cw0oVhQq2QYQknDguUuDLGRMPFKeEJ89eQ9QWnGyVrjoUUBGaqcgWG1cN7R1LMuS4w2gR4qZsGLkB6J4njWZL3YR6nO15xGj"
    "5i6S8TJflt6TbYkSSkdW5CLnJdF+tHDDHtHCdp5EJFYbR2hb1uFd7W20r3BDvnbTtjfwVpEnXECOXK53qucwmJGB8wQioyFK"
    "DoMO+/GXsDR/XcL5SbHbwKhjfU6yukyIdnv6LYpV2K1XIa1kpc/qHslOZSa4ZiFVJkddsEA1Ty3JBSAcwixBwrswFayMPX4u"
    "EiiCw55t086XMGP8c9u6dYv8Ok6r621098IVAcHuMh8jIW9xc2vPpxAOUVjRqYiU37QpnSfnFJ0+hlWEg3qRxteR/wTGzKy2"
    "Eawe+EiCS+BoMQnLrR472DnRFt6g58EGnWC22f+G3dkUzxj7WysrIqFpkBWiLX3Bcw12D8R1aGweS2iFHRDp0fl0inJeTs73"
    "SH4CRcmLijfG0QERlEUqJDJjZ4ZLwHvkomPELUk+SlplpF0yNDLUWn9RMDlYRWokXF1HNzO4vWUZFwndzmkB1Mf5ThPL+/Ya"
    "3kH2RueiAC7QCl15Xp8UzRfshKwxmA4gZUSgkFcfnHRxRYE3wut6ejjF/Xe6kA2jdZtSJTMxSM/QBc9xXKK4bwZxZPY2FnlZ"
    "btOC4TTGOeEl8N+YD6HT+KpcJhVONTUzZgLiI/jg5WskdHWqeBOBI57AAy9F1fbtBMbsazstux8LklShGRbF6ifRikxWGZWb"
    "ErgmbaDj1O47DYa5A15R3D17VmIXMIFpr/bwudWF1lBPnDAp+Lan6z6JHnWNpnjvh2hQoXq8SUCPbpDyaEtQbITOlou2j0cE"
    "oZrazCx8BSVrxnD4o7Uwprop1sXqVVs8U3S5zHC83oEM25rAFIOYKJleYCZsTTQM/vE79iOxUdvnKOiTDTdelEY9imI1yKhT"
    "4BEC9SHB6BrRHsugeyRx7khjmxSgZcT3g+3Uex4HPbN7K9IuKcup8KQdwIiakws6dh3WQpTS1HPHSE535MPno0CYoCNa+esm"
    "c2MTRMd4miS1Lrkx9bRt0OAmOQm0wGXYiyfBWhSDMK3NpM2q64s22UKi5fbf2cljEOGOz4d7gw0fzkPRJNwHebZ8P+HtCkhA"
    "x01prSztV9CUVT1j7aKAwoizrmWViCYTLr8QNaIMPBGaQrTOugbNdK9w57rSYYM2MJ4U1anQSi+v+NsKghF0+soOM7Zj9Gsd"
    "RH+Ep6q8cQw/hsS8dsjCe103QGFshqMzMYVQOQ7Ybj8vovYqx1bWC6xcoht0rNK0cVNIgAANkDcLGiwm2zjJa7c3GdrnUo53"
    "MnHBUS+46yarHUNZlWC/SXhgVdoZ+TBf0CmvFEVVG47cLQ5UrNnmHduIJxP8vUB9JzyRQSt26LJC69yEiFe/xGD+YE6kT6F5"
    "YzJBjImFFVtrJ8YSFtb/7HY2aRqHCPskmc7L5ALASQ4ols3zxwuzym/RuUQdscgiADQMX8FWYCMupcAPGU1yCUqtNSm3Q4L3"
    "pr0OlxE3E/iK8mnHaCJ/baYSH9qtioTWOXeVqD3sOrDm+QG0vFU1LuykbrQlkyiN56hmlnGaSkMTqG3IC1J2bNQh6apeCix7"
    "+j2HKuKm2Xg4uA/UYuAgcfaN66YUGRizAjPWStOVl/Sxx6Yhx+X8euvzXVQzGxSBwwwODDfBouvEeh/5/aO8wpec98bFoqBS"
    "htd2RuodshEaWrnEcGgzMewIwmsQbIl0xNIFG0gr41WiGeh0dPCv5YaQRma+mHFA4onwugUpzCwu2K/U1D3EF/AdkEIrgJQS"
    "JHrQDPEjkTuMPuUwbR40vm3dGBi1Ekg0aPUyxSXa9OVPDlPBM+xDXMItjLzVSdRUCU3kMwEMvYQ9QZZOWwqMP6d4VYmWArlj"
    "DBOUrXBKB1pqW/ZZe5vsvgk36GABvFLf9/S+DxKCpz6A/Anrcu0ugRvqSOJqSfNGFcuTAdpFUl68FSzyGyZ3v/RD3cSxotGg"
    "qaKGT6P5sra5W5pilmx8o37dPxue2cVtxDoOB5L77dzytfR1CWCC242Jjn4mCdGVSWAmjTogCSI2qvNSSd59i8Ri3ujw/GAQ"
    "YbzjdhjwGLVS37GBWuiktNGO62Fuy97CFD0LHB5dJuR1IM8Ma4X14xKEk2c04akkENH58xQ3sJda2IflggOShVwnJVF2hwZB"
    "d5OIuvhZyRobOlrbyiyfvR968LdDeYCXDpJ4lKTkPB8i59Ug/mSV5DxgG/BRSsZOGCNo2oGpxXqyKszdC0wGmZmliO00NluR"
    "83ZHNVOus/zcet43WVBAm36ajEigo8HN0B7h/Ba2ywojEDi+uvt+MPWssQ80ytgtSxPqWCwCtLXxIp6ZdmqdhAT44AAKgPNG"
    "NngcLhQItuxKQAGGbbrokJNGLYVGmxuMG83VBfvMkYs7Xo1e46aiS6u5dDRmyZ8kmWxmQFdDi8HmjT5xOyqcdprzgZ3l+eQq"
    "SUPb4WeNkJQX8YwwQRYXSxz4NE7SZcHcKE6ny8wLN8QEOyJB0AuAhzdcD+7YlHBw8ByigN40xEkbzpgeTy4TcpJOJXwDboAs"
    "gg1ukOb5Brzs6f4YeQKugqW82HPfM+rgUnyYo+hev65NZ+GN7jYrhY7nec5WULJ01pztZHPFbFVD9ARIHY0wzsaGJ3HBZlCh"
    "ftd07swiw9ASbxCz4fj2DOSjVKxQpY06ZUgXC1CA90X0q6SsuXtAwXhn0zBRqXILRusZNOznRxEtWRp4Q5zMLW4RMuLKx0hI"
    "PRml8ZKk470onqJ7S1FwDMQmjDpTMmX6jBee7zutzdStzQTh/yb8xhxTlNqm87hYECWywrVbRX+dl0XhvWViOQaaDFo5Kqts"
    "RI3aduPRtQgbfkLXuAJ+TZ0wfxWcxkBsdGPhAzw44sDDjjA4+r5/cgKPDH95hVtI1gLOq2iFB+J3NJQr50vC8Lo1X4gkjKJu"
    "TbBidQ63prigtHAbQu00eUmGAgYBl52J/gi9lAZO5saf/rzhlRS0TAi3u7aHiaiqaH2BJt3Tm/t59sTFCwR31Db+L1uatHVS"
    "U0sQLzAxzvhxtLPAAt8s3pXyGuj5F+cIJaWeBwB0wjBUQGH4aZscbZU2fJbPDZwyzoeixGdOvhVmbF2rIdgQgzFZ1yC+uAGD"
    "I8M10uANSRFvB7/gMDHTzPnjZeWs39WZZ7yRIy7Gc8lh1iFWw5+u4d+f9Z9s3HnDy/pnevxu4aiCXmVjLrd+xCasPiK548zx"
    "aOlEjE8yUUOJNLoT5UScQOvnPFFdTxK1BzmuXKDrLSGngr2wDUOmV9aR0FfJHjZzObRS1tbrwINRhA+sksC/Uvy2gjct25kx"
    "tSHYQ+7i+hE2dInIlTPQSousGdkn1hIvr5fted0cDY3hDLBcnxykUg8aH/fIuk6xC5983HIYCvVKqZPrN+6pXZByesC/sN9P"
    "0/IT3JLxZxj3Fm7VBxBl9OH47XIWZ13x0yEIXAf+XNjP04trP7TdLsi4bYLRRIpTH1+YhdF+7TtEF3pZH+kj1twj1twj1twj"
    "AudXo1w+39InCZ5Xva0P8lnOcNpHb7cHXy5Ei/+c9hgNGljDFIXbQr9O4I6hBP7ZZFshNOfFNVzmbPuCmuwJoUzyB8PkZNqJ"
    "feghqHB4yQ4SEOeKa715MjxA3DDm456K/vd/IaoV0t6dHcm1KEylD+JRqfuvOx9+4R5+Axy9SD7rA1iWubX1elqtZO0SAe0r"
    "EpDzQFKGoSC//AxnteoekgWM+8lMgVhf6yf91Hx5ovfSWGw+9X4Oks8GW42077ED5YowjSQaEItW7B2+t9wLKNDra0/6I7Y8"
    "ORKJNIntTg0+8FRCGD07UA0X3bVXtVDAxT/JfIwu0ogHV1Z5LuoW2g5YZLtWXgFwJLPLqP6qxu66OEI7TqvJzdAl3GBjothZ"
    "EZsHx7wI1e42W1XtZNdVBJ1cgaXQagnYEFYDr6pRbvNU7sILuFVK3WX9H9WrxmbYLmpZofUjbxETONLWJvQvReGx+AdiluTw"
    "uISzTazYgKyvwz3kxNlmCmjksz8pF7SWA3o22DsdAOPovz7DwSHFR0LI5NwlT8J3QOcDdkIU9HTwtn8qzGZ4pnxeXpA/Dq/d"
    "OaGzp9o8pjFOodcyzDZ/sSmYymZork7AtCmd8ueHd/3zs2NKhgSG+/7g3LITFSZXakyuDHIr12FXPvVROR6DLGx4/P5MXvAM"
    "7FbWxawKIX1gzod9SqhsJEgKKOq2Un0YfodoG2EiI+tueB8JWe4Qk0o4w1GsU4XLcPzHSGdXFC1N6ew7zfitu2a0K+ePv19O"
    "OeL3tMdw97z2rxyG5vQLinq7V3a7akTq/O/JDlerpdt1ssPbiBr3yA5XIUzGfbPDVVvIv1t2eJuA3TE7XLWyw/Vds8NVOztc"
    "3y07XHVnh+t1s8PVjdnhQi9fnx6fHwzbJPPZy0gAmuHn823KC5doptdFXqWJYAqhxe7RRvDPayNQXu78p7QR/FMaCWr7F9CS"
    "X4cnu+qr8Idb6MAUzSMIkZHeGP2WXOxuMOJ4QBRS0c034Bf7iPX8NhWXIjz0m3uERS2mz5+WKCNq4CQGDuoEdheEQ/UoHK4p"
    "HJ67nFLdouIBppsGsuISFc3kR3a+SAA0PsK5HU7JvypyTonyCau2Wbx/5NV0ThtFifOWRqIXRoK1KGXYQ+ktOGlD0vvpCdWM"
    "CtZX1lOHnjkjZw3jFxIOKrO+GERPREUChE54QNbdZbFbDLwLmFbWgsr1kHd2+q0V0hRxYKN12oug1Lc9h2LoYsRZ67ZOrRvx"
    "lrDnTsglfRfIJcWh4ndAXRKl9tMnovpPnjSlZ7W29KxXSM/qTthKnXRfNaXnFeBKqzR0dSOE0lpCsroBQmk9IVndAqF0s5Cs"
    "1oRQ6hSS1d0glII5qvtDKFkhWX0dhJIwBGYHkf5LSb/8IR4v0FeriNU8tTzHpdHv9J71fsB7uPMcWQoiD+28fFi2KBx3v//z"
    "zn4ovLPF9wdiZ5H+GbhIfgDiApkt48udicUEUP/AVg/9gIytxtXYTfz3sHo4OEEbsH0/QL82TMx9Mf0ewOShA5OHupfJ43ck"
    "2mjyUHcAxNPHH45us3ioW/Hw7mTxUPcm5r+HxSMg5uqeFo/fDQ/vgYi50Mw3p4PB+ceTwS6rwW8KY86xmIPeZchoQaZHGc9f"
    "YLxKGHDJHiPODrIylaIoZSDzVjwCodOJTB6kGKOc8wI9aDYhXWiBLfi0LG0IMDRwiflzi/gvIO1xPKmW0iZUaegqTI3TSaWt"
    "b+4qV4slptzDcFx6PbnrtoVq2vgiW4aGMUmIIH8DgvQ3krTKMaP6mzwz31iRElp3r9s8BZi+lK2hhMd8hCVDgLegK68zceUK"
    "H+NodLf0u2RIYGhkCrdzZch8sgWHhm3r2p65+C7YumXmAukocEt/AmpZPn1zftA7/+X8k4c7JCdpmSySNC6sncRL1QGqrf4G"
    "d/QbzYln3l3FALjKZZQBE8CkMdJDUL72SUQa/o8ZghuyTA0DO2mu1IzM8UnZ0EBYA8W9BwaXMme0k3Sj5PwOiqBs1BXCtHiH"
    "QM7SPS/ChAIWbf7L26P3+i3lLHKc9AlZrRpZbUeshMF06yDaDKCyuIDx2tNr27SNSoMqiPJ1stLzyAWEX7oYPb/TmhtyLbnB"
    "+Za0exGucKTtUdDiKZBj8Pbk4HKXDgJBbcHWYIhoEbyMEPl4pVlPopjKLTpYbovQBlFqWUJ4AB3P1/Q8z1lDJz1ZKbdU8Csc"
    "QW3PHYIOuMUi7kwj05MlRxCjB9wiwy8EKWJJm3AeQr/jCF/vv6HLdbL3Bk4IKm0FbYXRtliU2yN/2hWPS260/kWKYGvB8oXh"
    "cyiB7tpUlmqIyjg6sFkaSUTXn8pi/HQ0mT49HfT3DwefmFrSpxdj9+lWj4NhMaJUYVB0KamFslpWvKPw14mREFZ8iNuPS/N0"
    "CrMo573xJxaB1CexSD5FYzPCxD0lkC24z/bR+acfuZ7RFQJ1hOnDuIy8emgtxmo2CZdP5Ptq1XjZgRlI+oRfsEyJ+nNAhv4N"
    "lAC33LQkPFp8/Cl+CQPY4pOjqjxvXxq/vCGVlU6D5QX5PZ8mFUxrez7CBXBLXP9m/slmhNMqqirGQrdxusiBujPUVJUsfM4s"
    "Fnybvl7+9hsTz0/z0fa06o3HQDflwONkCQX/yXE6waiPJx61/w5z0Yf7L4C9mPHncrnQrgKX3qSIer57dBomZrSczTjY2nE/"
    "vBKKigeVWwwlJshQRBQmOcKz9R5W1epCi60xoROh6BKw2/nC3buw/3afPftu+6c42979vvtBr/yJIfPZbkRveQBWwrGIQSPQ"
    "58uCqu+cAqOGVf+QpHOTLtgG9cHgd/oAi9SAcgv/hgjxJdm6qh4g1VgDtwiNAGLcuhKzOTDRmmO8bVhsiYHXZe4sVgtOVuFD"
    "y8kfkongivzY3khOYzENVarLuEjyJfpJMK4Xp1KrkWGZHkHzSWoQ/oEpAlVuEcea2+mLEbYQfciFhWyZ76XwGwu04OZnwTAs"
    "yyUmp6f4poukSji1wNXhCMTFc1/SDas6TqckEMFLy9LyFbf6Nck1kq+xfKvnW7hQLY85iaFSpEY0ctira4Zv6FgBTVg/COpz"
    "QTHo6BLkzkBsQt7bR8ZVYSQO78Pwp7dY2XII80X8SmRoP50M3gKrLPLlxZYjDpHnqinmRQALwIhvl0CtpL0lJxyyU7Fm6A2z"
    "uhiZEh5hEdwKP9hImnzGxe0zjoukxqGBIKFifFjEJE+oslfB4o4AyeSYZ8FiE8G1lMYaMWoGUq4x5zKYrgzHobk0P2JLsC8S"
    "AwVyyGeEiIQpAbFDGwIWHezpzU+INFo+qantW3YkBAOD0orLVHADYDhOONkIJYHyKGxYVrniLEHu6xRLkzDAjtVb5vGlRF1d"
    "UH0fHEeIxvUEPsN49nAc1r58UZjK5Z7UzPbB8GrmersinKDJCVUyxil96sz37PtxboKwII6Dyl0YhuCio0A6ROO0ByPjsEHh"
    "NT7FxV0nZJg49zEm31XlE5rwBzxvYvcm9cp6aflg+jPL98Gn/tN5FfOTL0xFElpgN2uCLkjCiT3RXgnikVjTEF+YIDcZXoKt"
    "x21k+bnFpYQoSaov5ti6tD2miaQvdDE4VKGhwYSQDiIEp8hoFXKErIUG0amhkTgR+451AJvMdjL5QiGmF67s08DAJWmjnWk2"
    "YQkunD2SQBUuuSheVp8Jozy/yAXe2ODsrpOaU7oWZ1BzDP73f+l/uwZh8N9rS8FNyEpSvV4rcFIRYlHXWnZV6R/L4XL1Xckw"
    "s104K91lnC6NF8qaQihOFPduLBo+KwaKISHPUdtWNSb9rIY3r9pixzmDwOP5q5dhCsAcPgk/exLpT80j8SSyhNl/Zfn8Ey79"
    "p532QDUnfdFJp3JT5mYoNNhAE2usBgZSl15a4osc6roEs2UT1DqGjYoeqQPoMiMHW5xezGOczgh0bbyYUzIHSGlM4oWfgOA9"
    "CQoahlVXGUFdglrYvGF9i3+hLG0iT9gIffmEC08TUhAIrrjSQRgKg2dbh5808aQUzUiJYoSc+ArrBWLRtyQjrFWkRzAyum5w"
    "Az8J9XyCg/vEOBtoZHriahpb+koqn7uvZIOCDz45+quDEqThdWCR48n60lJto5OsjkQcymF2s6Iaojo35VREhhVnazx1xgeh"
    "4fO32f4BpoxFjPFjEKsR/sm4XnZQUViDkVe21E0pt+aZlRuAiyz8juSQZVZiproFMJCY6Ji4XjJeooLOgyDt8NIUtohC/W46"
    "PmhreJPoizSCsZqSsnejfN4Z/fDsGV272j1Teg09oZvmefQTpYMKrWQq7JFf5yh3OfBdhAmrVomhFq3YP2Fgc81PIT5jFyjU"
    "jBSCRiRWaEgGa91VAK3LKRHa4JUOo4aw5ZY3QpM/wjkkyCOh+yGYB5J9C+dR80N8GB4ckMlbrNc3BQ1BI94Z4eMvxS8sxnvr"
    "vJG4HfapBMXDoBHyD0h/zQXucnapTnW1oRUI8CtSnADitQ7k6tFg7SUNoF35+GipsO1SvinazsIDRR45V4oLRMw3GF9XovK0"
    "lO3TYTVdgXDXBDAdWs5lJD5Mr6FYOVaPVIGSKQI/aFjaQLGIU24JO2zWndYOH1OEIBKdOCPkR36FALkdKrlU+tZsUBaoFVGX"
    "CyaC1oYnV08CLqERxmtaNxaQZfmG5xcHGrpZG37WcPeRYlkgxU9ibX+CBUBjjof5kSfrYOMjzZWMBU6e8SxLj0deo6TCNGwI"
    "ja02HVRhYJAItD9l1x4GoGlFZ2HYUz6PTSfiOZFnGXFUQ11qjIJMxBeOzuHBoN3wIZ2eTdv8e1u0orXMXY5kX0I+dABzPo/H"
    "BCXG6kzMXhG2lRtcNc5prSxGh7Zzbki7cmywNsDWQgragMW4aorgF4Q9pt+fHrSMNldmRLgBXkVTnUiMqA7NROxPSlS4QdGY"
    "xAi2a/laWcN+q5fv9GtRC2TCS2drWihdH5kXNJAf/wU3gesL1QFbyTYeqq8ekIK0yqZuShq0twWAqkX5XpkhRghrkeBlJTIn"
    "AWTex9TBEsMyfH7sVkSuW73wE6zDR+PisF0ectWIEpPIgWkeouJEobcr8nFiBAqitAeOvEsJPjo65XIGdx1XGp0+YhKTKLoo"
    "0NDJzMOw1Y1iQwF66LyIS4bR9YpGXaUTF1srbCIov0sypQuceNWpKwQfDljcRQrhP5To0ye0VP7j/eAisYDcFych2x9w7lQN"
    "fdKU8LzISCZDB/nALILgW1BIxQPpjhcJpVWnzNd0ZSs/44jtLo3+mUPx0cBtsFBaONIgj0bOXmT5P502Fy/vGHgYEM9GR86l"
    "hAnwdYbtywsS46+1TRfVIexRpD3YoNOsQi3E3WAcguUNPoJUckO5oMLEriBhals1V/naMKnze37bI0AdRPZSLQnIiM0AfRy1"
    "OhmhHcWO0fFZay/4Q5Zns2xJYWRCB/e5pgUc6Zl4O5eSLio4H85wYVulkjiiAjqzXrWUpAky/aFyazmvc3bbcOkm/qbFhAQq"
    "545gmuekWRKNcHhQyIOKSpiGjWdCPKRrB8+NhxypN5vHgTQzmCSMAVSJC9mpdlRTr7lS2+T6uXG9RstZyTeAFgN/sUEc4gbE"
    "r7l8B1cncfVKPMqIs0RLxmukTTWm0RwDCZmj5EXsS0qpy7QEzsXmpTdNQhx1giG3tG8sHWmJbA9FMleTOvD0Wwbe9KvCYfr0"
    "6ZNFEVHf8L9aDs8e5fDskDeI88vRoFaOc/0rHKZYH8UFSGFp8kDJO6oBin7X5B3Vlbyj75a8o25M3tFrJe+oNZJ39M3JO2q9"
    "5B19c/KO+t2Sd1QD4ONvnrxz90rkreSdh6lE3pV8fZsa3hHefXuV8dtzd9RtuTu3ZkmruwB8hLk7QDkaRnEj4RB4M6lAUqSB"
    "+GAMw5jjfyfT3nzrVUhvyH60R5CMeFyxUizawAgJIALJ9Uofmi8JUJ8zQlV+n1GwCoLc1drYcd7rnZ3fm2TpWH0VyarlG6r7"
    "kayOfEN1V5K1Kt9QrU+ybsg3VOuTrMd8w79PvqHiQObDk/cE4ADjHPShKwSQQAJwNPgAff8y3DvWZ+f9cyAMR8OfgZKhRfD3"
    "IWYc+fs1xCxMRFR3IWZqj122XiYiS6XQs7CsJdqn0xTjEQ4RCefA4MEunpRI3ax87NzVaopOejS+sQyL2LQOa14aR3lWoB1B"
    "Wx7FIwKyjLPPep4sCGFULeAoouB2V9EvCJ5bW/R79ij1PUp9j1Lfo9S3LqH8I5oX9Qn6+otJkHMLxMeSOBdj/wsaIH74Dq2b"
    "cOrItozu7IrOLyHFVsnC1FA8fciZPEN1jvGnpwxi5MN4ZgtuC6LiJF+OgIpgyElVJ+YuDI0svHMZitBYyfZ41z998/r9r7/C"
    "bzbAtRvSrMy3Ma4HPtuQ+NaNIOpkX2gzX86ewgKwLljJtewq9TXabxVrwVqFJijsqZgocj0JPH54BCigcMQo7zl5tznEyaO+"
    "9tqpf88Q+3NX67d5PsNKLMNs3Gs9hLhzu8+0HgCbA+6kX1/HMyA5redeRvzcm3hsRnn+eUVzuxG2qfVh/luSprF+g+eEpdnm"
    "ozsYrzohNvqZ6wGYSfOhZz9EhFqnj/LPCdbs4yJ9AcgPxuoh2UomCbDpzW1Ym61WKy+15jN9hrQ9NdddgzlE13um3+UlhUfD"
    "8TwbHsAsyWokBu9mw9/DHOYFnKIPcCLac3z2IsLoW1o6/LGDP3CJdp9r/drMJ/FED8ppPB/l7al/i+9+H8kiPHtJK4E/nrea"
    "OoXj9S6uuvZk5+VLTAF99qIe9tsV5dtck+9A5pnFKaz7We+gtdV4sg7jCrYvLhFDsDTtLf6BRgqLD6wHh5iX2XXnQ+549Sf5"
    "iI9q6zlCnnmBIQbmi0lAUZ1UJjW/zbPkc36p6BpSIGOTJzLSPJYv4IrbroIuXzQbNs6RTUgPbxaIrJBhPRu+kE2A5mdzIqjK"
    "Bvu+0eFTRiukkA4sQdXEEtQ3YglG7YxadaOMUIuNu8I4zwKmEl/MSw/6V9OwGpIdrNRtiZSCHeU5JrBylAKQO3+kNMp2CqVL"
    "mwxzKVfmTTaA41bBIhHzH56f6f3jvffI+fuMO4dDV8M33cN+10dmPzhaI7FQucTCzpZoTm+Ge7BCH0N0Qcpw7MqKVbdkxd4O"
    "BHarStnTde3KyXAgLQzeH+Gwh2ec/tkA/XZwiZ0rBgfi+PXB8C2tMMaZSMP6sI/JpkeI2Qe7/P7kBASeSL8/2QdFFBodHL3D"
    "r3BzzmjDD4/3ac0IRNynbB7sHZ7tIjhpVYHUAH804KqE3gEtIXqOP5GAxbOlKR4Wnko/KDyVfkh4Kv1g8FT6QeCp9D8KhPXf"
    "01ykH8pcpB8Qwlo/DDqVflB0Kv2gENYd6FQHw9f9n4dv6mZrBH3Kjd4HNpj/Q0OB/n6gGH9nRIyHg8P4B8DCUPeG/2xyfXVn"
    "+M+Hw8K4Df5TrYT//DsgGzkwDHUfZKPfDf4zBEC6H/znCjAMtS78581gGNsP+g80MZR0Xmk0V+ejZW/8CEr0CEr0CEr0CEr0"
    "CEr0d6HDVFvtP6m22tNkPP7LhZk9/QYT+hmFEuNYUR1kanaHzGa9+RrhLzkKmryiqCxT/B3BP3J6Y2ooKQkjof9lC2mVzVhe"
    "I2EZgxZ7XEyNB0XBsj4nGQOlVyYer5N3jDlyqBgqD+oSdeYWB1nFnYHELiQfvnpyafE1gawNf3rLOcS04IQOZagwyvncB2wT"
    "ABTcfqdBUm1ewfwURrWycFrEJU2wjKXTpoP5cjb7X5cxV7eMx+NlEY/ZGID5FlVsC1/mhZomVeYKUIa5aGJk7Em6T5hj4Ei5"
    "U4iRUcA6RAp7hpXAMpmcYowrguX/CkPlhCkpMw8HaGNyaYwWlirsrZWwhnFCO8+BPM/zBTT3tqcPYsQ9eLtMJrn+GXjVyKDB"
    "pEcSxSlLFKet1LSOxLSbbEX3MeNuop/IGhokBWirbcYN6sE0bSSlUaFtZHNnC88yvR3kd4TSBxfQa6xhkK4oUF00UA4r4KxD"
    "m2RjzSdRkH/ttwCnm+XbrmIoCxGqlu4zqdWfDzN+onrKTz3Zh6xlqpnrszLVpxk3vLlLS0MB7T7VVcr7da+AqecO1a83DYRy"
    "bZi4bLQWtZFqk4WpNuQLbeFeEOzFBowV62H5s4ZbJtAWrU7s6bMVZ6Xqsq3jijDwKDbXKtq6E4b+xzIpZCEc3EX5Y4ARXEpL"
    "CqiR45ZhCWpbChehKhjFp56AU8u/WZl+40JvQhIZKZ9ykzezbpphOLxEtaQbTBWiyIlG3k3zJqMYYxfSFpiUBcdB43B4MZ5I"
    "jVROiMGDQX+rmyoWUby/xLbXty+Ar6uth6LkjTqhQ4Y2MrUcbNpyoFMbq8/SE09s4FipD6YOUiGmWrySPlcrmHzDls358rBT"
    "CW666gCgaLqcAvgJqh7s0SRsoeLCKOYEE5vXL23pS0KiRgsdMsb3WfIFT9U0mS3F3cslavWG/dRsEP6M6DSiSCBAXH9Z5fhQ"
    "Tw0bKc7SJQYseOO0d1BbNG/0AqTXnkawd93ClGmGKZs617OtiiUjLPUmD7E3WwIrjWQaPaDkkUorAqUq5wQPIVk1FvaK30cb"
    "PxCaNN1GC31z/L9gEkqJHS4XSsZLFX+7B02jxgNd05pRJzRMTsU4TnSRkqdiTotB56bRb0GAlZxFLMh8mSd4dCqg2Byq4bU3"
    "2LDyutQHv34A+oclvSN6Gdvjgt08xXlcKqqXXphFjhcAGEU+oxT4yOmX+CL1X/j66iUJO0QJHIKhURtLQtBDgQi+gPfKDXuV"
    "KjOeZwlQOOAMpgxq+RoaZJzOEE1zviDip7i8PY7UTZljN9APVFDsHry+xBwpXHT8CqdGpHTEIJeUUhQXE4UtTAyuKBVE+WDk"
    "cZe3FfARyhfZwE16i57XZFxyvAFzRTibGPmCXi0p9uftKFgd2hQEByqF/hYXyzMUatB3LmESBssWwHg2y8UWg0zgE+gfoUDH"
    "ddvY+N1UE0lSfDqbfN6+SL6MltOn39SN8rvP9GCxiLOlSfXbJAVGeQ0y/aN5/tE8/2iefzTPP5rn70974Q4WwND/8y9Z8hTE"
    "4Ulqrr5RN+NP0r8+1ad3BcpvePJnB+L7LNI/AQ1HooOBbStfwtRJyZyMqRvMm3xqAyWf0ujgEBy6a21rzOMxpyN0OoCzs//e"
    "1pWEp+A68cWAT6iBnRoSVs9OeUNmtCHBAwiGHUBfNaCrRUuLHOkkfiFNNXNpUZieYJcs956ZMTeyA+0T6JZ+6QRwSyib48qL"
    "1sA8hc6vMgYRQuMKWlEsdMjEA392vSFJXawOWUinOtYzDoAwxQbUdGsQyyyoxhqPqRU7Cqm6JAgabL/CrxJT2kq0iCqa2kpM"
    "9EdKgybjAH7KUbOofbBDQWv7oOUScSUd9lBgEgxYQSVwq+o23O7RhrSyIajnm8kWv5pfoTDKobZkZsv4d4rUGcciRVq8CvqK"
    "VgAZWQYqFQnGyOURAJQHFjmcp9G1S6D38IsOiYomvZnASGh7ynlywRhpU66FO8amN188+z9bnAMfgiFQQ8uKxFASueeweqVt"
    "EZocmQwWgRS4WuvBOP2Wf8yXG2Swwt+Kja1w1+MsDKLMMX7Unw8LBsIwNYTN4JRvb78YMTZV+6idkWi0waJJ46R5CEH6dkor"
    "TvnodbxEB69pQ6UsqHjKMcYkglv1L5DForb3yiFQR/b+BxppiOYCQz8mS1176Gg2oM+8kkFmkIVBKR91c3tBYXFKDnaXA0Wf"
    "pC4gHsR3Hi0jH9UmKG00pingSJMQnigSSIFWhFpIvWCmrmYVGh9YUMMiuRrT2YNpf8iLzy2iYCFmxKgEJ81fARAIZRruAvDS"
    "ybQWMQLeXMZJSuqVj8p3AP+Ih+oMgah+ObrQCBCz5C0MAkOyUlXIWybWzISjtahFMaaDSUZCIJCKFNu/QKtL8oVtxFt+FfY9"
    "xhMuSLnRPAFspupaA5m9xWygNbADd3YyvIqh0YhpFXbF+LbkOUHQXE8MMKcjR7kaDt5lUrLFlapVWFxRAyss0fRMKGSbw9sk"
    "jTlvBK1+jEF+KV0Kb7Ft73mbHls6Na1dfxD8Gssnq4enWfZOoLNoYQqDVhR7Pw0GLuNJwXWhaYDagbYQxEGkhRuhb2rExrst"
    "u+mEBjGNx8QkooBHukVtDUps9n7X9wJk6c4db94Bn6rWgW9lgT2Fl7pxYGO1PWHMGlv6T1rKeW3oLfh+1eCj4FKgnYbSGlJL"
    "tsvlSFRdaM/KHWzrchi2chWoI7GoNcQKB5lFl/MmbhEKKkiVqXs87yMDizm1holO4WU9bq833Jw2pC3m944sw0smhQtY5Bli"
    "VRMcfkrnyEb4o/CxzGzeEt6CcNGNXyixCYauu0vkwTexIke7wj4wKdWNqY4oE9XMB1YUKqliQxmScMI2QRYyJh4pT/i8JJZW"
    "nKwVLnoUkJHaKQhWG9dtwqgrFBKNPS6IXooY+YEonmdNVILGQpoGc7XnEV0SF8l4mS9LXxjREiWUjqzIRdAt1gmEe0QL23kS"
    "yWB+RMCa4V3tbbSvcEO+dtO2N/BWkSdcQA6CrnfqjZkgMhqi5DDosB9/Cdk3U7GJ1hniSOANrh8Tot0eGg9Z5vSWMytZ6bN6"
    "lHWnMhNcs5AqG+CSOligmg+U5AJ0vscezdEevwD4McuzbVd8CP/ctsH1dUjIAACyxc0DFEhnqELfA1J+06Z0npyzW4bN4owM"
    "6T5x+JGNuPe6+9aOxdFiEpZbPXawc6ItvEHPgw06IeP4/4Ld2Qw851XsgG/Rg8QK0ZY4AsLdwziHiIM4UMqzAyI9Ose0SGIC"
    "JgXyyz+BohDEFK6Ix+tlQVmkQiIzdmYUoEF71EQeJvcZrTLSLhmauKX42WBysIrUSLi6jm5mcHvLMi4Sup1TxFd37qzE8r7w"
    "4m+WW6AGC4QdKYALNMAGaa5J0XzBTsjaQekAUnIFCnn1wUkXV1REVnhdD92kHx3+FrqUkgrPtNuUKplJHuUsxq+JyInivukZ"
    "lpOti7wstxlnnvyhS/JT0N+EzZ/GV1hiCqeamhkzATGSf/DyNcWV1KjiTQSOeAIPvBRV27cTABxe22nZ/ViQpEqOfY6OCE+i"
    "FZmCUI6PFhiudseE5VmpirkDXlHcPXtWYo+Ug14dOXxuddGfiBoYk4Jvm+Z4H7/kTfANKlRLwAnp0Q1Sni0ogRrbctF2cogg"
    "VC8zEHNCdycla7p6/dFaGFPdBimLetUWzxS9DTMcL5V4I30DqynDFLOqrIm+TjskfLfmRBspWLJjFHDi+hwFfbLhxovSFsiT"
    "jToFHiEMeMvwnEjaaNA9kjh3pCmAjRx0E1dhrqz3PA56ZudO5PO7nApvIw6akws6dh2GqVpxmOQaOYcrkMWJQbkpCoQJOqKV"
    "v242JdbVFWiMp0lS65IbU0/bBg1OgDvRkUg+rDyMR6rNpM2q64s2oZAdt/+i+OFWbxwdnw/3BhtUw4EDlND1LzFPjNBu/4W3"
    "KyABHTeltbKu8rYOLUiIvuucvf7Qmc5llZJFJlx+IWpEGXgiNIVonXUNmule4c515YpJFZfuQdTOoBl5JcBOxeoT5Ss7zNiO"
    "0a91EC0VnqryxjH8GBLz2iEL73UD7zeZejqDLHPmOWC7/byI2qscW1kvsHKJbtCxStPGTSEB4pJiMsiWkhSTbQqcdXuToX0O"
    "o2k4R5prreOuI/1qL3Ow39qGuAKTskY+DKZxyisF+NSGI3dLMFJD27xjG/Fkgr9z6ajgRAat2KHLCq1zEyJe/TKZ1I4O6VNU"
    "AHNisslyYcXW2omxhIX1P7udTZpGC2yNGFgmoesycW3VEYerVcWyef48CmyX36JzibxWQWIrGetZAGgYvoKtwEYcErcfct4B"
    "5tEpwXvTXofLiJupw623RxMFxZoknK9bFQmtc+4qUXvYdWDN8wNoeatqXNhJ3VyLR85RzSzjNJWGJlDbkBek7NgwPNJVg7Js"
    "Pf2ey3LgpgVFUzlUxjtInH3juilFBsaswIy10nTlJf2PjFZeM+S49GFvfb6LamZDeMPI1ziVJlh0nVjvI79/lFf4kvPeuDAM"
    "QgsjoDFG8pehlUvMuDYT48GQgy2Rjli6YANpZbxK5AArruWGkEYmpRUmoerhFqQws7iQUK6G7iG+gO+AFFoBpOxxEJaVo10I"
    "FoncYbp2UKmJxBfrxkDwm0CiQauXRC7Jnz6aUpBy5NDaEUfe6iRqqovAmtJ84hL2BFk6bSkw/nyB7mkJF5KKxrIVtRoALfus"
    "vU1234QbdLAAXqnvewRXLPEx8JStfeIugRvq6DpA+EEVy5MB2kVSXrwVLPIbJne/9EPdpMDPeDxvqqjh02i+rG3ulqY4qgYE"
    "hFVD6pnvw4GkkTu3fC0TvpXBYJVTyWPwZtKoA90gYqO6r25Wu0fesXA+PD8YRJj9vh2mv0ftLHpooBaPI210QWV8FADsEuhq"
    "eu0DrFPm6BPDWmH9uMDOFflFgVgQlFQBg1vagOHChJjmDXs/50rAdC25Tkqi7A5Ygu4mEXVXDgQaDx2tbWWWz94PPUZactaH"
    "Axuj28Oa5FmuQfwRKBtuA6vCkbETxgiadmBqsZ6sCiM7A5NBZmZpMsPQ8q3IebujminXWX5uPe+bLCigTT9NRiTQ0eBmaI9w"
    "fgvbJebsVCV5x7vvB1PPGvtAo4zdMoxY5rq1uONB2Htow8e3bUiADw6gUGtvZIPHEwzPFleCi7cnuIlGLD3a3BBRHwMe2GeO"
    "XNzxaoIuaii6tJpLR2OW/EmS2YBrT1dDi8HmjT5xOyqcdprzgUV0S6xaGdgOPwNTzi8Q/pyL7yxx4NM4SZcc5A7i8XSZeeGG"
    "mGBHJEgQUO7Wgzs2JRwcPIcooDcNcQ5RXozp8eQyISfpVMI3Spf4YIMbpHm+AS97uk8pDrgKH4Jo9b5n1MGl+DBH0b1+XZvO"
    "whvdbVYKHc/znK2gZOmsOdvJ5gpyG5YEgdWPJAmD6h7SJARp3wbX07kziwxDS7xBjJfVB93no1SsUKVNw5WIWTyNMJ8ggjsp"
    "a+4eE9Qt5UwbWTBaz6BhPz+KaMnSwBviZG5xi5ARVz5GQtrIUyFJx3tRPEX3lqLgGIhNWPJgiD7jhef7TmszdWszMVNQV/gN"
    "jN7vMJ3HxSJ1iW/ZtV9Ff52XReG9ZTahAQ5rQXUV2Igate3Go2sRNvyEKH3Qr6kT5sPciUBsdGPhAzw44rDBjjA4+r5/cgKP"
    "DH95hVtI1gJOyaHwhTB0D7+7tik0Tms6X/OFSMIo6tYEK1bncGuKCyqrbCOYnSY/TQxmUACDgMvORH+EXkoDJ3PjT3/e8EoK"
    "WiaE213bw0RUVbS+QJPu6c19ShEVo01wR23j/7LFpZ9ITRV43pHx47AR2J5tB75ZvCvlNdDzL84RSkq9BaeUWkhozKSnbUpM"
    "UPlJzk0pWUWkdhlO+hBmbF2rIW4RJ0lZ1yC+uAGDI8M10uANm6jTCn7BYcLBS5w/XlbO+l2decYbOaRWoVBK70z80zX8+7P+"
    "k60W1PCy/pkeP2jjaNaPTxQGhAoQlo253PpRkmddLq9LD6alEzG+syRsoPkHWj+jddXsxP4g21Idt4ecSiH3bRgyvbKOhL5K"
    "9pCYM6XrFSNbEU7NwumrJPCvFL+t4E3LdmZMbQj2kPuaKHE2W2LG2AyrUWbNyD6xlnh5vWzP62+T4g5i4vXysplHsrNDd/8g"
    "GX1cXrpqSH2OzfmHTydRnE6i9TdfmU+ivEEwuhfQR697FHdOKvnqgWiJAGsll6wNOaKa7gKZW1hczVI8htClOmr2Y6lNXtYr"
    "rQHpoXZHFgMg15TZWBpfO810J4LWEzCVNcKsW1TtAXJjVICYcr/cGHVvwMpQF1ercmP0HXJj1G25MXqt3Bh1F8iUrtwYdV/I"
    "lFpujPo6yBTJjVH/MJApD5Qb4/H8MA36EcHkEcHkfyaCye6zRwSTRwSTRwSTRwSTRwST//EIJk4mOfj1sK/UL7/q9xUWDGE1"
    "GK/m/239Y3vEfrs4iDuOPtYgqFuA3EsKF+h3RrQGQnrgvFIYKbk1KSQ98XGxQbv8uStg4mzB0q7eRFfBeG7Gn7vrJ0ATI4x7"
    "LMy/bLl4v2087ulvi5gzTSRxAI8Y0DnCQPFPfvktgv9NzJi5D74Ff5DVjf7G2q1Vnks1rFpjykfKYKPWn4QwMDNT5RfVf1IQ"
    "0TyeyDBdQpy0/DkIKYH3XfAKjF7baivXHOQTNmipMJk4XAPY7cHbk4PL3d7OvwYTJOgYAYeBYcxAMIpoO3nCl4m50gGOCS8r"
    "CYro4XDNx5P4orIkaPYb5lcx8bJti7qcNCU7q02yxw2NMjhSGmg4zL7UQK6/7PEb/IJI5eayqkelau2OulNYuzDw0YdOomBQ"
    "z8bPsrnH7QNzLmmRsBYLDMWemXKt15AYLJN0IrkwNlivrL/Bg+StkRNukVrs+JqLiKTC/vX8X3v6iIK9pyzwSCMo1S8vwsNG"
    "xAmNJujNHhn2yiVpFQ7ZlPa60+bin3z2bGo61ph2+yLimwENZuaN8bDpFRKxxBc7oUfC/Vxv9cyXqoiD18ivxYt4yxqqWvCX"
    "X8NLmH2+ZMwlT7ktkZLurb41ydvKVehWsHvl7w6MCtUjybJqT8+yYtL96jMgRSelwC8QN9DJMGZ/UoWqjuQjX9LgUcdBOYSD"
    "r2yYDTFo10MJW5BSP24oKRDYujW9q09fJB5oICZtsX0pja+uGWCIMBlKC09UrVZuMG223KhHJ1lmJ+3IYMlkjllWxHnRq77A"
    "eP+SfRThKrGIjgTJXn3mQJl1FV3YauxepOGABxpmUyMNBs48KE8Rh2rTiHedZcakkoldwNWp4uKaE1lqZ2jLp55lcQWSAr1L"
    "L5LbW1ba2vGZexLn9AnAlHKNHvDAWEm6uxwqWNIRLuQo/7LB7ncqbhEQvFceT6OhB9rg5vYC6n8Tl0S1/BzHWUL+iC+/Pf13"
    "2aPugpHo1LJU0Cc7142ndENeuRsttcV6lmW9YgaGTAjImZSdP+Httm6BS+fB2Wm1Q81wI7e93fnu87Xefc6rcJRXxtvNUTwA"
    "PQxmvCkcHjR2Yu8FVW7fcoGgeOZo0fl8JcYFXDMEhVxFPBHn8yXGffBho7CCVLo0vVnPCjhs7WXb9xX6FNHKE48x4Sq9ZgW0"
    "TXjwQ7QZsCVBvMwU8YBXE+S2pPLcKcvDcQUnBpv1HJd05lByq99pIiGgL5VWiWVzFOhTrBOCvlN+DvQoTCmaCsqE3GnvbA2F"
    "3JOjt0p52zKHu0XeUnvKbEAcaR2i74p/oPRgCVtDQu+YXDeoP3UcprW9Rfob3SoW9IKL5OGt6u7P+ojabwuQPby9l5cL2NLz"
    "ArT0zgefPXPlrJ99Sz+/Q9zaH/Tb1GSZPo2RG5bbJ2aOdXR6nSP9bht+fK/72QQDCkBXSGfoj+meFD6v3y6v9aCAU3cGlyPN"
    "sEAcqVn6210pFcewhgG7wFgTtMm12YXXi7z+HKkOa95tcXxkVnBxfAqaaxnybrPiQSNJxQAEE4W0P0z8Qg07CGWTtK0aeh4D"
    "oJDBEKaC0lorHM9bH0fGxoVZO4SPV1KNoJsaxIOPfaMQuVoAnA31UoJaQLFcEXMRfDETWBkn3vjxhAq/HQBWkILWnIDnn7Zx"
    "VKvjpFQ9Tuoh7IaqYcAN7YY2MOpGs6FaWRwqKIdF6WU7rJ2Fsbk1OwQaYySLAObkbNJo2ruWJDb3DKcztmzvgVGvZYNXksDa"
    "FLww8kAsAwST4cWfBScqhrYD1UqFYVaCg8ZwFMNxn5FFKMWxOskF1mAXHdhkr7SmUwfeYu2PBPXu8+opV3Q8531zaU+d66Rj"
    "q510rwT0/1zivfeaMSjWrUGWIEb8xF2XsRITswJt6dIO4/pUGh7aNbnCjt6kOMZkdAEjf9Z74aC2dnrf9Z6/2FIPFoMgfbgl"
    "3+k9630f6Z+WsNw7ROufRY3e7bcvImIipKF/Hcug2AJVs1LyuLZxON/xRpMhMwgsQXMxR/XLNcP4IhX42NlKIjEzZa3FQPFy"
    "d9PbhkqFOUIOYcf69n1oOTwtjFVk5LNkAXT8JEFzod6LJ4lZfqEvmIH1YMLX8ATL4oeI4HG6TFPMShDsUs+B6YO3oG8BiUb3"
    "SSyAHT+R1/7j0jYxAYZyFs9jWJB4tJxMxJAhvn7ijwRjB4NZ6NdwcbDMqC3y2Zi6cw/YKftVfOXkdmaxwJkcL7XJ14xGI2fa"
    "B7yZ7C/5tQCKeb1RjjwFGtnXa+yvsysia9Aoy5pTuB1VaXmJbfKKlNRlOk0kStpGK7ZZMCto8H5mzKS09l/bUNKMt0AePI2X"
    "aVV6oF+WfpUNcadQFqB+5TRm84Jz+QlYAwZ9hf4/sgPTRKy+6DYGXQpALM5Q2q+Ved2QaI2NhjVL9ALBlt52yYMqMCs0rw/1"
    "JrefxGsb2NwUM1BKYRBg8ijbkC9+PL8wLp4oMHw0qcqz3svvPYoiVrkMiQpd8UOM39PoSmSa06YqthjwsxuISN20L1ceer8L"
    "DdF1GqLaNIQbvJmG6JCGqDVoyDlQPfSd8jXumiF98wFJw0JfAltGwTj53LXaP7xEIg2SIUrSfql53IcxbcH3K9a4U1JvL65f"
    "ix9+iNR9Fzcg0Cps8C6Lawm0umFxf8rnmX6NMgyTkD+aS7hTr+EqGubgZzFQyWU5NxLofxjPsmWp3+XpYlaIHfttYWb6NDdp"
    "Pi3dlp3HGaJGdd1WMW2Ht/Wmm6Y8OnftpkmAmY1CbO/2C7enL8K9xmUMrtx3K/Z7fXVrJWRV3SXtEGk2ujZkQyUeT1S8XH6P"
    "S8NZ8X6rZQPrx5H9TbiWXPd8Egv8SWsW9OkJ0G78ZJFM+INz4IkfzIw2brXyXNMoObqC+VPtGqu+dd3C3OtL5g576JtL0LYs"
    "mieJlOp+uiee+IbuyWkF07oG6rWUOw5egkYwh8p5EskV3UwfUmG+kE0lQr8/zIsMnKtyiCLx32HcoaQKOduQ84qu2J0u5VDd"
    "nETzkEElAfDmnfVDG1ai7q0fNkJmO1Wfhm61plLldCoH4LC+ToWj+ltrVM0DrW840B3ueI7echtzm3Per3vMKC6LixxRVxTh"
    "/7liGvbQBkkWEv3f9OHXAipUq5eGJt5Qw+u6NOnXpHmruuZdM3ieD9/UKgczE/jhB2b6yAUPQCpFNtliFDvyDFwtuM2u6IPl"
    "DifNxOjmRaohNKDUasi01ahBTlFrDcdr/TJ13NzaJjrUBpJ7LTRyd12A8qY63IySgbHacauWeK1sOkWdtBBp6SsCRA6jrlWw"
    "xvREcz3DW7KUoCaO4QpjXtSqmJdgaHZVwlSIqB6FrYJZw8xuG9stJcW3bfj1jVXFlQRmRy6+ul5N20fo2mYoCLlvUdE/qrDZ"
    "ZjQ2tHVboQIMBm2FX5/1D/XB4M2bA466PoPW9o6P9NvT/sm74V5XsUoXWx0WI7Bh2KujrGtLYYO45bsP7/rnZ8cU/gwr9P7g"
    "3FYVb5caCKKpI2WDoOEzDK+6OQiZO4s6qw6oIGTahkHfWs9c6pefDE5haQ77R3vtAOkaDfoweH2iWiajHVD6AuvFY1bLY1bL"
    "Y1bLY1bLY1bL/9islo/vf37MY3yk+I8U/5HiP1L8//0U//hkcMSJjN9gXM95DdfBAWyHOTtkRFpZMmUXEZuX5Kz7Rr8+2/dQ"
    "DC5KkHPFurM3giCMXEhJgv7Wb7TgRTAansM6Yc4TYiUJ8rL9gtOt2ErGn2Fj2KM1Q3TZ6/U3nSFVu+gO/zbC2qNkmUOs5Bjo"
    "TMplP4FJ5stLigl8v3ewBefYpDMsm3pTW8BQp6ZEALXXJsuTSh/G4792vvFc3uhnVZ4RrGY5jhcXZuXDz15G+g3Mcpwn5fZx"
    "mlwmBnGDLmP0c3e88yLCmFTgNftFniLTxbq1QzTC6nMTr57Gs+eR/hhnWTL+TIAb4/nSkPul4/kdeX4/BpaCno6yNFnZGelG"
    "ddZ3I70H+wn7dWTxSydPBhVFsp7BXieYG6o39+Aub8lsOxeEGoI7Q3H4iO39ZOijC4MX8d1OkYa/egC5Blr5etHGHmKSbuD3"
    "ry77iY18tYADbTxE+c8HGIu486Sl+0k5DQmHD8BD1AP9RLLCEzpqD1YSFBp7uKqgNLSG0HBrZdDjD0edQgM2dhe5YUVtULrV"
    "64oON5QHxXbWlx5urhBKq76eAHFbkVCZ3m0yxDp1Qnn3bhEj9LpiBLZ2kyShv3nqpInT/n8csnsHVt9FyG3CH1uhUvnf/4UU"
    "+YUOSDBwmPE8A7Yzs/HPVBZieN7f0v82zcvyD0kV92b5ZS9f/Hurqe/0H+cxJtm9y8vsWv/bZ/prjn/8wczS5QxTGv79Zk9i"
    "xClBSGOkIonFPTcYxyHwThR5RaRKtdM/GUrTYmZ2JaIxBpUtBb+xRZ1gKK1yuOUNP0DgXQzFnLarmV4XEafblwN0EadFTggs"
    "9R76dhpFaZBAoqtH1R0oZy6+lohvbvNjeYlKTu50MP8+s2a6LDLoUlAfsRhiM01/VTGH85VeoJucQA5NOUxNiYPpFFTzqIrZ"
    "s+08wY1p3uI5YeSGG90mAWG3RZtDX0mbWtH1vJGAR+r2ms5AdY6Oj0I02hVKn+q/h9GfCoxXk3O1FT7iBpF3iwjZUd06UR/+"
    "Y7cDTMMTN6JjdfeRJVfoNonUrc4LtyNIFb0zgxrU+4P+AbSFqh1Nse3OON87+M/zP96ElOGTrU/NLCwU4kR/uv97cZoACcuS"
    "GEj2MtOHCVatkcpo5BGBzxlvb4yudS47Q5UvXYEhjnQ0Za+RZyXw3w4bILUAD01Y3pr7UPKRA0BzJxThJVDNZOpeHVUlJIlt"
    "1OwGQVFN57BLDWuRxZZ/WLWCLeqCr/kCbTM1aDl/C6NYkHX32vp542wSgO8IMQjKcSqqtVdJBSFC1S1DSPij3FmIHGZ55GKj"
    "FCqcXKWKgFOTBsS3hK/6jCaqMrik6MrDGtJ/C+hA1N/2+UsKFQfBERj6Sn5mPicSSSdJ/nJkBEkcZou7GXVoFJm5sser6AIK"
    "ge3h/NYChG7EfKSShQi9ScERnEEJTzAS4XWnaxavZUBcXE3suiHp/Ji8skjDPhKRaUuGnR5bIKfdsmDD/9kQdpQXdkCU0/sg"
    "FyJxFC+1FU4Hp8Of4aOfoTkkKIPjNywbqeGb2rTe9X8ewFxAaLrd8KKc4SVsoVYtHD+gyb4Z7sEKfnTSd+g+X1MdsLqAupmV"
    "rFQElLCSyPKS7TozWa0GHR/BjjagI6mRG+dNS3l0rI5fg0RL24G8UBrVh30Uso/QSQ3n4f3JCTCQSL8/AdkclkMNjt7hVziy"
    "M9rFw+N9WkTCpIQlf3sMIvkRyf9wGF7Z6B0iTmO8wEGCsEUecajAlvD3znqCROlqfem37m8ROThUFEGJN05FcIP7xAhKG81U"
    "qdVhKCoo3y4vvTETSlLt44BLko+QNy1TISmbb/qn5RaS+D0ywOkXu73d3e97Oy/JArK5u9VbNW8/IlUrKcklbJA92Qrj+4hr"
    "TMlw4TSstKUIa5axZxB1Zc+HTu1Z2G4n+lr1Xa1eQ921hrWVsXPd5cluf/9s5zlPd2eLxouL0uuqdYH8x8xyCtsMoXyY87kN"
    "DwbnGDZwYC46Lym1smDtGhMdxR1r6VoUal3HlWWq7HG1xAJrw9mdBPNL/32oUGEwWIThXs/p57f08weOHD3GaH8KqnuwcC8q"
    "gPUg4V6MdFHdIOTbcK5ONj/Kad2gibtoB75NH/6nGmBYD6h41ALR/gkVjxWaB3oe9NvT4/cn6+sZK0wn99QzakFS6mv1jDBs"
    "auAg8lx5NudM8WZN7ySt39OgFjkQVRdOWIfPCoqT5s47ygkbmKYs9RnqubrKjqFlZah7R630KlcbPaz1EXpV6pe917WosD1v"
    "5f8OM9jQn1GW5jqiXLhrfTaPiwumLT/l5Vyfg3KUmqrqhBrsfZXFRjUtNvoWi426k8XGmpjvb7FR3mKj72yxUSssNvpmi41a"
    "z2KjOyw26ne32Ki2xWY9wqkegnCqJuHUaxPOrxWz72CxsTK0+hqiqf72xhn1oESzrCvapeLwkqRwhYe5PFxASHU3IVX3IaRO"
    "Lrs7IcXTGluAD0dF9w/3ThoC3ctHIe5RiHsU4h6FuN9RiGNP2ytMrUUN9ARhnAqQq9B79wumaJV4tpcLR6h+PRiCvGdxpBHJ"
    "aOd7/ZOJs+0U5/w2TlKuKYs5np91f4JJSeomBOwncbmdlE/qIHFtjB9owyHH1dF2CEEgVOHh4s5NOlkBoaO0q3nWTlsMMc9x"
    "2HXpM0g5FAwfV/SsCckeWraVDqTAIJ1MqrgEtdDJzI9WEoGcDYqAIew6NIQCb3p9g/OsmYz4lWg19azCO8HVBJjD7fDMO8LV"
    "rICqUa7qoWTNdcPV6IeAq7E3mevz3g2vRrvsyjvlVK7IntTtCyf/gjun9V/wgT8QamtezHwV40WMD/whTpeLLOmNsdPxvGcm"
    "S6VqW2b0b4hJ6/A1aht2ZWQT/OLrb2BG32A5KpNcIjNOTTar5tdSzNHuIKkciPQoic08R/ZEOKKAV4h0O0qPlMCpLjgwacQO"
    "0tXoY6APWBlLDUGGcaumOsnUj3jrXN1YW4k9LFZlaxLwKtUup8Pclul0r5C7OLb1xDpagE7tEar7QT7TcFAIdyDAv3PLh6eM"
    "8FEEBB4W4ISLRmDFeGrtTf8/HI5erQ2JEWhWXm6M3V0NL57+ena+r1QQzcmwAr+SQRUYhr/bjQSxQ1PFML64klg4TlFHpXsK"
    "c8Y1+R0zCOphduq+GQRaMgi+NoGgFs/GlavunD/wAOkDdhjK1pC+X2Rdw1/BdSXvE1anuvIG3sRjM8rzzxHlDeApitbPIPjq"
    "BAIfqqn/VvkDOsgfUA8SELieB3CtaEAMBVR3yB/Q6+QPqPvGAXYHAaq7BQHeHAGo1o8AXCf8T60R/ve3yx/o8IXfK3/g/z7I"
    "P/Xtlj65huuW6c1sWXAl9EL3zBdC65qjeJJtKSmbWErdxAt6A+WahxqG6vc0LMq57FGoCqobEWN57FeoJQIHrrzDloMqQEt6"
    "VqL0wbV1EAHqNEeMFg1s5wwo8ZwMF4cxvIJsusRirhjwDk9s7n0YYhl6U5v7+CrpZemW7eaIwD9ToL4YGQQyGoitY04oIDRp"
    "V0URETRgdP3Xez0towHZk/B+eRJPSoV1PsfJBSonpExFKJM6fGAHIb2gGtJh0XapUkLuUS7bxMBG3A8+mmRL6B3JLBd4yey2"
    "C/sNgqOQlSkX7o/OAIIZG2bAnqjiMQX8n3atTVYkPVSB4HBcxr1luYXa8il9EOmfkwIk8ySWAJZ5ANpUIsQ7dObUArErB9og"
    "zAkBmxjojOdl+aTMZJwT3NalSfMLUmIqEwNLJvG+ytVrg7p3b4zsJmd2jS/zx9LGQTwq6TVWco/HVT4yhcOvQLDEa1iNKOjW"
    "veJ60vuggVTodccTyeolY/aMzBib+DW/qC04d4Z+m7Bhb2B4ky9BzqOt2Tw5e0Prrrrv49OLcvp0i+4DzhCdNDFhpQPfnSYI"
    "STuLMzFKKHtjahgvmPpzZddj24ZHDEHRgvNLKNeYMwMMGDHkmzOhjuEOXJDpgjKVzCJYQRg9mj1A4pQ5yglgSY824oxFus3w"
    "bCFinkDloC6F57Oa15/fx6AEkp+2YDnfkQjPM4qocEXkCqDDZ5F0r1z3FPRA9XxJf3l7crBN9XMqxADnakNcjomyp6TKSvKb"
    "KWkgtqiBbU6AuE/5T6f67YvIQyUO0Hoq/44Ry8yWFIGele74Rzf8ln9+yP8vhmDwKJ71XvaeIcTZUu/0dusvWISaFwio+WHo"
    "Pr8WtMed3nP75gt8N2zAwqC9xHeBHLTf/a7eFzdB/wiRUP6F72ZctwHLBddf9U2FrwZX2r6KcKs7q1/d6er1Gina7pZ0vVPr"
    "Gobyr7bN8H04ybo96trL67xqFwu6bby786+2vXXe3a2/69rCFLHb3n3eeHd37Xd3WZkkDafV7852Bjel411QXvMKbiKWJqCC"
    "cPXbRkYAhKpfmJiLFIIy/6RoIHYL/fD1eCge7OQALn9AXiy8ZoSBuYmU8YCnIqrxiKaAAMKajFleHbfAwk6djT83tf8QypNN"
    "Icre4WA+rgIT1UxE56+rLYC8eTHCekdWkIHOGGeVNDKnEtEyJGUb5pCmwwSK2T/j/AMFwnpy/RBO8jRBP/0ExB4ggSBDRHJZ"
    "xGBXH3UkyeDjmGtOuuxMMvLEsAI5WtKArmMJbE46xbUPOlT2nkV6nl8hh49c309KKWhCzVV5OqEn/cc0YRqfrXC1IXYhN8QN"
    "r9PixqO7Kc4+OzxekpJg40pQf/Vlni6BheH6XM1zJvcoCLmVJIkCRDCG5xPn3kIqv5TGMyq7eSiDv+5p0D4OnT5py3CTJrcH"
    "qgzHwgYKgn5PH518PH8HislXS81yamp+xVYpKLtzwaFRN4gYNm3I125XCraNUdpoxeXl570fEIRWiqfAHUM724WtAmQBO72Z"
    "rT0wioNvj44oRmsYyop7v5oi35bIQzSKeZhPwhMNS66IZEJaAeynDNzW8QoqrHkc3vN5cGN59UoX68+lOOAI41rY8cZVMATF"
    "W+vtEW+O3x/tc0itVMTQoKGe4d+76m644ztixbbt9N+eDlijpsTw6spIVcjVu6s2N2BxMYzGrubQZySAznIcyId6c0M2wWxs"
    "UZHFkvw2CDjnPWfLsh29C53QCDa2AoMh1lAMTGUWtm5Vxhas5W5Pn9UdLRI1z1no1lxmnSr2xPR96gAeJHahK/IeuYcMi8Xm"
    "C6hUJQhlkc0soKJCEdKGdLJ9haTDZVXk3rKH+SNxev0bmhMNCpcCWG2tcMBVLtL4WmrAoAx6UWAkr8ABkxbFNYnbWRzh0rqt"
    "jFOqMFa4xAnfjPCpSFlTX0Bu6XDCIjzpWB3qjL8Tt0hYmipSSc/0CI+2mUJOaNMMU/88AKt/wZD19PN7+vkD/XwZMVCZwpzy"
    "SHLB4Se9u0PvCiw+vbvzPUPk0096l/Gtd+ndXXp39/kNR/zHrlC2DQEJ9n7ktRdWydZRSog9PnA6n/dQayNTAzlC3cmS55FL"
    "NnbbsXFbTFRRx45MWVOAti5MDOCrLD4qAdHH4sJB1sRUtdGFR4WwIA54x7wdGFtLGBg3U27MEmdCWTdc4Ni5SNgVhgAO3L4e"
    "FYmZBoUmaRhWIiJkCkdqYZm+7dEtTEorQNmFD8fpxkGuBmuNVVTJs8cS5GH/j4MztHueDtA0a9NGyHIYmmbbMRvQwGuxxL2B"
    "r/vw6aBhpJQMlFo/aAq2VtczMubVew46vgVUUdmIjA7bLgVF9M9D66BwkA/DgwOCYbGRIJLGPDzdl4QdsvZigsULXmO2BeOk"
    "wgwfyy4GNsGGIy+gs9Mz350bY6cFeDUw4ymna/fPVF/gF2ktMAPkIxmDXZ4J/bVSFvKwji79p579c4uBVJ6FxfjOcskWwaNQ"
    "hXhZ5VRoj60d6EjI0Dm4vMCz5zwnalRQxpU4OroYD/T1PSU3zCUboZsL+XimCX5AFiK2vBBwLyORQnPz5IJMhjOTYa0DvPsI"
    "cQ4fE1zyX3IQYbDmbLUsjOP2uO3ISR1d0qsm71yrK5ILsSlVFXB90QXPhSLwL/YIkfvZfYsFEJkjtj08ynl4KCK2uKSkQZiY"
    "HWJkqRv5dhlYBpbyh55+fU38h5JCMI5Q5KxaoA6LG0xDItem8oQL1nmEvMAmkK4jMii3TCTVDzCsqreHCKItSQuviVxQ0M/V"
    "neQ3bti+Tr+fHb8/3Rt09GNlxJ01xb7AFLK5wX+glAeKDotsKp9OKe4OtKrvnumzGPhNPot1H07UEvbkDBHRNUjVRRzpvb5+"
    "+eLZix0nJap7SYnNbVOtkkkrRUNdFw0bgZKbG2Gg38bW3WXFmtW3fQwi+0AYf9kUHre99KjuJj3qbulR3V16bAabqrr0WAsr"
    "/xoZsmUpdxSGjBJheJ6PBo/W6FKvlq6kPy851GdzB/lBLt7fQoRodvV3kyJqoZd3kCVAXpM53E+cCFP+9VcJFQp2MRQqSFyI"
    "vGxRy2vGv8Mp23Ri9dXyxIu/oTyxWnZxQgTnwPJ9IUKJhaTgElUeZaOg6rlVaVkgWuhscDTV/mxAJxAdk/DIbAokhyrD4FtE"
    "DjgaSd9L0lFe0tH3knRUncFxZdE7CjuqLexIa6vknbIh8KgbBB69rsCjQoGHSjhT6WGMcnalSjZqRHZDp/ksLwNaF1dKigV7"
    "vx98UyLjf0oP9+bVIrWABhSI06wpFCyHD6rNnal9RJADLNyuL5HVa1D+LnIZuWxulMjYXvzw9rSGc9xh4aku53ggdOlA6Prh"
    "5Qt9wr5x/WGO2IX7BYkOzj3eJ4vHDkg3ONP1bHRqfenLMm42+gdSmFpXCvsdDHQ4U3WTkLWGhU7dRcZaaaFTa8tY3RY6Pntr"
    "yFh6lYzFDhDVbagT70iXpU53WeqU5Ac821l5ejtDO37U3nqmbrWe8VlaPWl1o5SnOSoc2SUKuQn668ySuBPPtkMud6cDKRyl"
    "AWG2mWnES2JxdL2ZY7CpBRz46xI9jluv9EZt7EnZBFMtV5xoFZxoGGp9iH63LFPy2wd6FlcIJuderXch02lugV+oS2LpmakC"
    "0uqmppYZop3SeS7RM4HB+lhHCp2Whd7EKP6MK+3MYdCpgRkj7ent7j7debbzXIbnl5QLpXPkg4CsuRh4PKpfrompYQxHfXSq"
    "Y3T6/enBKy0caj5JezyGHjz+NBzFxu9sP+XV/d9iReXpsGxOTLBlTJV7eAeViNr5WyhE9Y7+3kZVXqi7mVZpBl9lW+Ve/84W"
    "Vp77/1g76130IqJJDNijkjBy7UIi10KV6D0wPzNhzajsQAxQDYxHaTgI6YfGiK5ElmcA96GxwXVXhGNj38FunW5C4F1Rh5KG"
    "wD15dmUw+BMpgw2cDDU1+9mTMlTadENpUx3QOzqA3iGnNvyFYSGEq1AXfZjOqrzB75kKe7JLxrASiTYRH7u1olDEGNdqLjFA"
    "Lr2uFTsNYliO3uu3VIE31SckkXn00rcniOVNBWFXLpGPLeXzwIdhhS7B4EYxE+iyXJKfn1MSpUxvYReG9GhUY09gbjMs6FXq"
    "byP9gtnI96sl27a6rO7oGGiqy+pujgEiWh3KsrqHZ4DaqqnK6l6ugVBTVnd3DcCpIJ4n93ADI2xOzjcwsBOVKo4qdih61NbI"
    "669qHY+C0MhABwn0V3Vn/VWH+qsNgeNhoz774RZ1liM3z9+dHr9/+w5jL9V9qtW3axLqbQkPdwHwujMAvr8A+bKYxIuIomKC"
    "UHeJsmunjd0KjtBRGfSusAi2ILsAIXQA06h2etl9oBGUg0bQ94JGUEF5y44yhx4PUjLYu7dD2e2AZcAjcyOchvYlDpHRCf4O"
    "ELBm1mFn0UNf8NAlWmHalaqlW52BtPWO7K2HfZAG4AfIc+80nOPz0/eHOpDuQGAKsp8IEOB08LZ/ihlBqgF+7VNr8LW1odQt"
    "nELUkRR1y0CdHOckyQDq8taChLUahOqeNQjrYL2qC3+hI9GIkpGGx+/PBLChVnVQ3bHqoO6oOvjr4PR4GzYRn6asUyFSb2g5"
    "9gcWrEHIVA3G8x4kqptsrUNJxJDTTUyEbChLNprpqZ2kYw3QkAaa5s0HXrkD38TsvPt5V0Gy4M3oRB1gIO5sS6af8jl/ax7y"
    "1YU21R0Oub71kKvbD/k6pTXVLYf8oTLXXmzp83F6Q/Yc1oQHuSHFJDD89nhkimkam/HctBLrqnG6jRljD5pa91Uw2yh53h9m"
    "W/fHqE2QbqVCO2QH/Pb/OPztGhzcHfG39Ur8bbUG/rZeB39btfG39X3wt5WDufu74G87iLE742/rDvxttR7+tr4Vf1s9EP62"
    "M2F9VHfC316Rhn0z/rZeA39brcTf1mvib6sbE5fvh78dmiPV+tn3XwvGtQp/u76aqg7Are8JwK3gCN0HgFs3AbjVQwJw21Tc"
    "OwJwd4FHq7UBuPVKAG51KwC3Xg3ALRNXqxG49R0QuFULgVv/zRG4ZbIqQOD+Vm+OYL7PQwRufYsdMEBFXAuCW3dBcKt1ILj1"
    "LRDcagUEd5hi80CC0XcguX1+FNzuJLjBF8DyjEcSuosQp5wQpx+FuEch7lGIexTiHoW4RyHuUYhrC3HPH4W49YSk7xG/asiu"
    "PYx/QmluusTcbljiAdB+dP576a4muKFjUF58ULlt36yQKE041u3XeV6leYyPwTBBOECEgoJee3tyoLFMgIETUqj+sszi+cJ8"
    "pmOSosyC3LmcFEsz/pwishD65ZYjuNCTmN7jicNunRQ5nAU4wdy6Gpk0MaNkRhHVeIqWGf6+GWNoUob+p+18CtdjS1+ZBF74"
    "bTkzI5P19Fli5h51qLF0T032tCSAnKd2izEMWf3/jGTnQw=="
)


def licenses_text():
    import base64
    import zlib

    return zlib.decompress(base64.b64decode("".join(LICENSES_Z_B64))).decode("utf-8", "replace")


def show_licenses(master):
    """Fenster mit allen Lizenztexten (im Programm eingebaut)."""
    import tkinter as tk
    from tkinter import scrolledtext, ttk

    _b, _bold, _t, f_mono = _ui_fonts()
    w = tk.Toplevel(master)
    w.title("Lizenzen")
    w.geometry("780x640")
    w.configure(bg=THEME["bg"])
    ttk.Label(w, text="Lizenz von ezd2lbrn und der verwendeten Drittkomponenten", style="Section.TLabel").pack(
        anchor="w", padx=12, pady=(10, 4))
    ttk.Label(w, text="EZCAD ist eine Marke von Beijing JCZ Technology, LightBurn eine Marke von LightBurn "
                      "Software LLC. ezd2lbrn ist unabhaengig und steht in keiner Verbindung zu diesen "
                      "Unternehmen.", style="Foot.TLabel", wraplength=740, justify="left").pack(
        anchor="w", padx=12, pady=(0, 6))
    t = scrolledtext.ScrolledText(w, font=f_mono, wrap="word", bd=0, relief="flat", padx=10, pady=8,
                                  highlightthickness=1, highlightbackground=THEME["line"])
    t.pack(fill="both", expand=True, padx=12)
    t.insert("1.0", licenses_text())
    t.configure(state="disabled")
    ttk.Button(w, text="Schliessen", command=w.destroy).pack(anchor="e", padx=12, pady=10)


def run_cli(argv):
    ap = argparse.ArgumentParser(description="EZCAD2 .ezd -> LightBurn .lbrn2 (Texte bleiben bearbeitbar)")
    ap.add_argument("eingabe", nargs="+", help=".ezd-Datei(en)")
    ap.add_argument("-o", "--ausgabe", help="Ausgabedatei (bei einer Eingabe) oder Ordner")
    ap.add_argument("--feld", type=float, default=110.0, help="Galvo-Feldgroesse in mm (Standard 110)")
    ap.add_argument("--einpassen", action="store_true",
                    help="Design gleichmaessig verkleinern, bis es ins Feld passt (Skalierung um die EZCAD-Mitte)")
    ap.add_argument("--rand", type=float, default=1.0, help="Rand zum Feldrand beim Einpassen in mm (Standard 1)")
    ap.add_argument("--skalierung", type=float, default=100.0, help="Feste Skalierung in %% (ohne --einpassen)")
    ap.add_argument("--vorlage-groesse", action="store_true",
                    help="Groesse und Lage des Inhalts der Vorlage uebernehmen (proportional eingepasst, mittig)")
    ap.add_argument("--vorlage-rand", type=float, default=0.0,
                    help="Abstand zum Vorlagen-Kasten in mm bei --vorlage-groesse (Standard 0)")
    ap.add_argument("--vorlage", help=".lbrn2-Vorlage fuer Geraet und Layer-Parameter (Layer-Nr. = EZCAD-Stift)")
    ap.add_argument("--ohne-zeichenabstand", action="store_true", help="Zeichenabstand nicht an EZCAD angleichen")
    ap.add_argument("--schrift", action="append", default=[], metavar="ALT=NEU",
                    help="Schrift ersetzen, z.B. --schrift Romans=Arial")
    a = ap.parse_args(argv)
    fmap = {}
    for s in a.schrift:
        if "=" in s:
            k_, v = s.split("=", 1)
            fmap[k_.strip().lower()] = v.strip()
    conv = Converter(a.feld, not a.ohne_zeichenabstand, a.vorlage, fmap,
                     fit=a.einpassen, margin=a.rand, scale_pct=a.skalierung,
                     size_from_template=bool(a.vorlage and a.vorlage_groesse), tpl_margin=a.vorlage_rand)
    rc = 0
    for src in a.eingabe:
        dst = _target(src, a.ausgabe, len(a.eingabe))
        try:
            ezd, st = conv.convert(src, dst)
            print("OK  %s -> %s  (%d Texte, %d Pfade, Skalierung %.2f %%)"
                  % (src, dst, st["text"], st["path"], st["scale"] * 100))
            for w in ezd.warnings:
                print("    Hinweis: " + w)
        except Exception as ex:
            rc = 1
            print("FEHLER  %s: %s" % (src, ex))
    return rc


def _target(src, out, count):
    base = os.path.splitext(os.path.basename(src))[0] + ".lbrn2"
    if out:
        if os.path.isdir(out) or count > 1:
            os.makedirs(out, exist_ok=True)
            return os.path.join(out, base)
        return out
    return os.path.join(os.path.dirname(os.path.abspath(src)), base)


def settings_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "ezd2lbrn")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = os.path.expanduser("~")
    return os.path.join(d, "einstellungen.json")


def load_settings():
    import json
    try:
        with open(settings_path(), "r", encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_settings(d):
    import json
    try:
        with open(settings_path(), "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass


def ezd_fonts(path):
    """Alle in einer EZD verwendeten Schriften."""
    out = set()

    def rec(objs):
        for o in objs:
            if o.kind == "text" and o.font:
                out.add(o.font)
            rec(getattr(o, "children", []))

    rec(EzdFile(path).objects)
    return out


# ---------------------------------------------------------------------------
#  Erscheinungsbild
# ---------------------------------------------------------------------------

# iot-simply Logo (freigestellt, fuer dunklen Hintergrund) und Programmsymbol
LOGO_PNG_B64 = (
    "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"
    "RU5ErkJggg=="
)
ICON_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAIAAAACACAMAAAD04JH5AAAAwFBMVEUDCgoAEyFp2vpeyf5kz/yW6P0AEiMEHlNcuPZftPIA"
    "EiOO7f7o/v8SVmUKL0wka5VXCQgcT2kNMEtSla8JXQ8fTmoqapEs6vYzpuctjqpQkrNGdJoQL0wziKty0f6Qsu+J4f8JIZ4r"
    "Zohho9KJ8P9ZreMLfvEAAP8eSmksZohiB2FEd59TUwoA/wCdDAI9rOFhddtIjbX/JhX//wA2hLI+i7E7pdg7y/taWmRqqgDh"
    "Wew1fMIAmTMAqlUA/39VAI34HmeCAAAAQHRSTlMKWCb8aSKaFHAq2lUPH1slC1yhKAeeYhgkI2Qq4VyiItMLmNWnoAkB39AK"
    "bwwBCY8RlQMBjsxmaAkEDUwFAwQJBYNBAgAADyhJREFUeNrdW2l72zgOJhSTtChRsuz4ztGkaXrNPbtz7fX//9UCICXzkO24"
    "affDcvq0E0cWXuImAArxf7Jm/ytCGpe1+Bf/YIVY4x/84VkA/u7Zff5NFwAohcQcFv65X6L/xTfcv3+5buym3GxKtzbbdtMA"
    "IyMI3wrAtRAPXgg/SVxFUZbFsPiTjdSM4OEbkP9ekJDbpmulo54tRiPLBuWC0vgm0tdlEZH2EvCc4L/dE45fX5e4uB/ZtnSr"
    "kMWAoCjxg7oW6iuygd6k64F+OdBdLGy73y9o0We1LOUB1dYZxlfYugBF5JHgek3vr+uFtQv0ALY3QXzM2m3R2DrQDiktOKt8"
    "JQAF+3uJ/L8vijWRBwMMS/xTQLBJ97+2DgQlN03zWqvH9zZe61nFSsvUrTg4IAERtxDwduuVopDbsn/RF/O/9O/Cf1vN2vDn"
    "YcviIOXvQ1fZ3NfSqcv9/T26Ba2/zEXjlxxHSc9tul8HcHBS8efNqi69ndSOAfpy6gr3X3gAjQaASK9PKDmQL2qKwSScReoL"
    "9u1er/dekkUtBFwoO+i1hxSo5de9EMFs5pRf9RpNO3D09cs34NyHrP0btqibl6iB7m2ftm/FpfsPlqydFOxlLgGUAf5mUXcU"
    "3U7mO3DyRY3nI+rwRXrY7Fl4dY1Q4LynPrasMmrrxLDW8FIAM1GT8Ii+5fD+iiSQHJN3jvLl4clSkJUYWrTp/e4ogl9/meW6"
    "lykkiIWH8JLgpMnblQ5ALXH/oe+ZxWL3jsGlYXA0FbPkThlBbV9mwi7RwLCjEuc3C7wekm2aplutOvxH9V553NTe296hW3Fe"
    "o4AeXJf3pQJmAIw8Ypr68+dpdVjTXdkZ5Z/9bgRC75fP5ggALYFdF5heuqQ7NTllumLCa8prclhFx0Z7PWYZfQZFVnXacIVL"
    "ddEXiTZ9E8aHZllUA/FpgqGqdnM2m1whNCczKFc45Tg0KOukpXp+RgEPHlaTcPfTnBFyZUfZDGotOTaCOgVAO/rSGpW8hZJM"
    "PZ1W7zwtpImiZ9JVIAX67XQOuQzAKOeUFyeN0Eff9YikAKSoJn6zFXJbov4rY9S669AYJg4H/7q6Vfm3lUdQt6c0gPVv7Uwb"
    "knSjnvS8lrtuwwGTdYSeRoe16KZ1NenxZXxGAIaTVlKDY/4AOs4hAv+r+wCtO8/w6UR23vUEeZD3SPjQZPI7q8ItovuUnWkZ"
    "wQI1fWzhgY7p10Gm16uymla8N/xDmSE4BwdhPOI/zbxXiGqFATUJWUpwaJStGj32g6hZAXJLAfO5cuLF144qcQ8ZmnlvJ9VO"
    "jURth0DAdQ4ATdw57Mz5WLVzClZ97kzv90ed+F+CvOSt58JU5ZGpYQCY3EOeKDe1z7/i1xvT7Pwbl8q8IBkzZisdD35OuXUt"
    "FIclyYlrjEBBMQggWsrsUP4kfYW2fDo50y4BUHPWlkk1NZAUTMjRlURFxQC0MMpl0amFKHPLxlVNGziivQc18tqgzIBApWmQ"
    "J4MKiY8fIKAPdiGgTngGZuXUr1AvT0z/hRZ366RwayIEqOokaZkxugXtfGCK13Ss/xNp1EtSqcDvFN4aHfD3w05hIX2qH6ZR"
    "WrS9CcQAFOtThfStuiw3dzyYyrkJ82Gt/Vl/n+QvP0kWQapjiiTJ+if4WH4RAh8hQ89B5SNwxyUI48W1sAwgc1HuHZibC1CX"
    "Hk5AMQsmRSA9BkAZokR3GL2xIwBSJQAWrMvvGvIvcCn5Agxb72QeUiIrcb4g3BLaAH22TalMWQE38DCSYswGcc1GDwnk++cV"
    "s1AlZ3qiVdbBnq4xbhADmhAA/m/LLJyq42dYTW8BmP1y5GRQew+aJ4hl3QSiUXSKLWUiZ5DEgaoVkSnPHG3ncG54qYMfSilV"
    "PizF/k1SSa89CNwxhRKR6LGOGThtRs5kn9Djf/h4d3f3Btfd3YcPCv4aVQWOjdU8ia9U2yxlE4Rzn4hAJOCCVKjqRorGWqnl"
    "26erw3p6+2H0FAmKWfBZHPZG+FtJAOYJgBJCABh22ANkwUHATKv526tkPb1dqj9yBIZZIBIOWAYwkFsrAiD3fg8zBwBcQBlN"
    "IW/urvL19KhEnIYJNgRiQalj7SpJ5QYRaHLP9wgABgA/oOHfkglOnapClF0+jtG/unrz0eRFiH9LDmZGh+dLpdDqUOkHAOib"
    "7uu9t/WZD0PsRVbOMiGMj8unq/H15jFzV9+hKtNGFKQA0A4OD0sqBixiAB0fMlaQ1uPU/Bh9lMLSeAfRr1+hIQBVZ2bOUTgA"
    "DSuB7mNoy4majhQF4zBJgKJQdEgCuLs6vt6qVGevYUc74QyVPIWrwRl0/WWv3w8cipEBifoyADohRYkwLN+cAHD1UaXHaXBh"
    "GXIAhVfwdgAQqZok4P8wggEEFvt0iv7V25ssIqxchgyBDsQAUAiUosQAhK1cHIL4oKluTjKAWLBPhLDlnHquIv9kub/SnyYY"
    "QBvrWsuBsIkZ+oMwP56mf/Uuc4iWAaxUGMZUywCE+xDagkUwAiCNImDuzgB4c5OGPsMhuYs+hrXjgEPrAOi4Fu7NNyr0kASu"
    "zgFIYy+MAuiomVD2ImAAcTFe3fBpNHnZs7k9B+AKv6OjU6PhnOAxBoDOGP/zn40CmHsAUZTX5m9nAfxoUgDTMQDEgUUCQET+"
    "bhzAz2cBvIsBiBcDiBVeLRlAUu2CywH0HCDV0IMnuARA/LKLRTBwAEYA+FrNGABYeivQIX2tzivhb+NWsIqYCTrnQAtpIpED"
    "eAZzdaEfAHc+cRzQh6IH+QEHQPcAwi/OwAGYp52Js47oKY1GHkAXAxCSAcAxAEKvJ5kjYgAfzwD4u7VjJzR06r8GAJSVAwco"
    "H6AMbR8jdwBWmIJGZ28zPx0Nrz5kCaTLbMLDsPXR0GcArdhzTpody9AMCq1+SLLMyWkGJOGLmq+UEQ0JiVfMDnOyYtPnA5iT"
    "lnIVKw/bIeYRUYZllVLz05HAJBW4mjMiBHAdAFAOQBmkZJkIlMuIVFYAUR9PZAR3kLSR0XQ5JSsg4oDilKwYRnMk5Yh1rDod"
    "Z8VF3v9Q7457wTky7Dp5D59wm1gENZ0Oh3qQFiUBKCO3b8xvE5+VJiUgZY4huLuB9JyOZzgqMlVGRZXfhgCUdqhcLfjnw5wS"
    "A+CDySQ78l4ruLkbk8Kbp3l+PvZFpp0KRUASIMPTw8asOxuGztJwtXE6kdk8EZWLRrzBG9GMnM/BHc2Scz/wRMTzobDBAGQb"
    "d0gUi6CyeVeJCgOpR3z6MFrEAa7WJbUfYB3gw6lH0PoKbuD6cZ+cFFV5isW9CvMhhHD3aMbL6K58j9knxACoRBIA4LKR7Mho"
    "Qu5JalBUJq3ec1prjJlP3/J6926lQI+V0N83rlppIabP+21DUC2XSVsdckDozhVpRzptnLxRH8ivoGUQ12JcJw2iKqpy+22j"
    "Ax+LoKMhHdsDuBaNKxMrlfSPwHdIqHhEZ8eGf85FYEtX5wfINICoRQAWXKZrE0fesiVS+n60jX/9/fFSIZ4viX6ZKDHXBMu6"
    "iaqHHJ2KTSwrqtKwFfOsZNZ8yGtHiQ9YOg1Qh7IJJd6GOSC1CicDFDCAMgHgCiyTakGH5JeUy6M46OvlXVwnpUBER9GoM4hs"
    "aXlWK60UsiPFlzSmBXMhgNbRfwwL/dy7KyWfhSOtRUUiAFRBjnNa4xsPnbm0WO3OpJP4vEEAGh6PaRPNVGrj+gUJAFA1F8uq"
    "Bi6ZkfxTCNc4oxplBIBmDxBAmfSmCZhzBWnPjsIp94O3L9UBjN/oOpaO/sokTthQIJKyyRyLUdzLkZvMluZD0w4fO8sGwIgM"
    "nNS7XDjdD031SJk3J2l0i/v7Mi0MqwMCY17GBeACF3mArInOU0U0bJn9QiELSm6l5G3vpUew6tR5BLiX2j1fLU0aoMF1ZqQY"
    "m0xBFrhmDjzHzUAvBWodL5uTAyCagsPKTzRUuybbDOa/JdflRwG45johGDITbgL84hwSh8a42DQCYD7Mk8yV/o8Yaw9jNjwK"
    "AIDnx6itOjjXvgsBrfTDIhPZt/a+y2vzML8N2/efsif8YNsxn6Ld2PbiUBvVQSLrXk0jI6UN/bgr5KGkum43ZZP9Pevd902w"
    "fqTrxAwJGwlkE0wIRB5GVaopteApTCv27nMU/S0NNPkRjkk3kp9pPyC71UcBaNFyoBpBoJ+R3qoOJoYqfHJJa1oUwRANqepu"
    "JDnRZAHsbJuTk0zWuaMa0jEe6m8btQvY4GBU4RAPO20qN/2QR2vw8wFWnR7mssyCvm6qo9kEIE4P40KTaT/DNB0+qybFXMGn"
    "MRLW0z8zTcYJq58+TAH4HHA+NkDlGDLZdYpz2HyOCfxouoJjEyC6N0c3ypI08l37Ha65W6janZjIkP+kfo+PSB2dySgBwMMY"
    "0d+en+dTHkFdH5kOFA80qaHmy261/CwnFQpst5w3PPyHX3k/1sT17WIpn8/R14IHaouRuJS5fNJTa8Gbo65phnx0uKaX6/29"
    "gPMAKDWQh5lsGOvKhtdI3DCl1u2R7i0dULf+CoY4Pcx32Ny2H+lWF2diI2+z/c2MFuBlM9LAdbPRqabL17ruLzw4DwjiQgSn"
    "J6dPXSSZUTqjKfqumb7K7wec0IWHjb/FJOHo5NY5BFQK2dCNLJTmZTdeZrMH3TT9cL89gfow92lHflcvig2tLxElIhjuEp0Y"
    "iYb03kP4cdkPdUvxBTcsZmhZ/Y0ZjKCcj+qTBhwe7elemPWXftj/X0rd72PT39hpG7TIcdeodTIY52oH/nqCdLcbLoXgB2Og"
    "2fhLbHTNBOJhkizgWhc0iNgf4r4XYP1l961mVIfhq1Sluy5DM3h7cJNIYhjiTPFol+Lv/a0GGtEXSrzmohFdYaxlD0HWpeZL"
    "jaofK0gLCnSnoxguW9Xbn8RrHSkRa+rwChmyxN30zJ3APr7qtaUw9crbZjw8rGDr8tn14Y5fi4v54+4A0Ew83Um4P+zfGmNe"
    "B0C760LAcm9lEd11lMFiX8EVFj53cWK9pRTylZHMA+g9U9Gts8uWI4s5ZFl47qufXgnAWdia5n7BLgp36dNd9MyJMy+Kr3Tr"
    "NvE6zsyktft7yttjRnjVKJ8bld5oeo0KHKn+oa113aLnNq+y3Hddg2m76q906cCjfhUGpAcEcPfXrNUNNCO3nr+CGE68IvSI"
    "h3BIEUGLr8P+k5zhBDRn8Bea3H8B6Jq5Kj8PWfoAAAAASUVORK5CYII="
)

THEME = {
    "bg": "#E8EBEE",       # kuehles Hellgrau (Werkstattwand)
    "panel": "#F6F7F8",    # Arbeitsflaechen
    "ink": "#1E2830",      # Graphit, Schrift
    "muted": "#5E6973",    # Nebentexte
    "line": "#C3CAD1",     # Linien, Rahmen
    "accent": "#1C74AE",   # iot-simply Blau, abgedunkelt fuer weisse Schrift
    "accent_hi": "#2687C6",
    "brand": "#5FC8FD",    # iot-simply Blau (Logo)
    "plate_hi": "#1B2839", # schwarz eloxiertes Typenschild, hell
    "plate_lo": "#0C1321", # Logo-Hintergrund
    "plate_edge": "#33465E",
}


def _ui_fonts():
    if sys.platform.startswith("win"):
        return ("Segoe UI", 10), ("Segoe UI Semibold", 10), ("Segoe UI Semibold", 20), ("Consolas", 9)
    return ("DejaVu Sans", 10), ("DejaVu Sans", 10, "bold"), ("DejaVu Sans", 18, "bold"), ("DejaVu Sans Mono", 9)


def asset_path(names):
    """Sucht Logo-Dateien neben dem Programm (auch in der .exe)."""
    dirs = []
    if getattr(sys, "_MEIPASS", None):
        dirs.append(sys._MEIPASS)
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(sys.executable))
    dirs.append(os.path.dirname(os.path.abspath(__file__)))
    for d in dirs:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return None


LOGO_NAMES = ("logo.png", "iot-simply.png", "iot_simply.png", "iotsimply.png")


def load_logo(tk, master, height, icon=False):
    """Logo: logo.png neben dem Programm hat Vorrang, sonst das eingebaute iot-simply-Logo."""
    import base64

    p = None if icon else asset_path(LOGO_NAMES)
    raw = None
    if not p:
        raw = base64.b64decode("".join(ICON_PNG_B64 if icon else LOGO_PNG_B64))
    try:
        from PIL import Image, ImageTk

        im = Image.open(p if p else BytesIO(raw)).convert("RGBA")
        w = max(1, int(im.width * height / float(im.height)))
        return ImageTk.PhotoImage(im.resize((w, height), Image.LANCZOS), master=master)
    except Exception:
        pass
    try:
        img = tk.PhotoImage(file=p, master=master) if p else tk.PhotoImage(
            data=base64.b64encode(raw).decode(), master=master)
        f = max(1, int(round(img.height() / float(height))))
        return img.subsample(f, f) if f > 1 else img
    except Exception:
        return None


def apply_theme(win, ttk):
    T = THEME
    f_body, f_bold, _f_title, _f_mono = _ui_fonts()
    st = ttk.Style(win)
    try:
        st.theme_use("clam")
    except Exception:
        pass
    win.configure(bg=T["bg"])
    st.configure(".", background=T["bg"], foreground=T["ink"], font=f_body, bordercolor=T["line"],
                 lightcolor=T["panel"], darkcolor=T["line"], troughcolor=T["bg"], focuscolor=T["accent"])
    st.configure("TFrame", background=T["bg"])
    st.configure("Panel.TFrame", background=T["panel"])
    st.configure("TLabel", background=T["bg"], foreground=T["ink"])
    st.configure("Panel.TLabel", background=T["panel"])
    st.configure("Muted.TLabel", background=T["panel"], foreground=T["muted"])
    st.configure("Foot.TLabel", background=T["bg"], foreground=T["muted"])
    st.configure("TLabelframe", background=T["panel"], bordercolor=T["line"], relief="solid", borderwidth=1)
    st.configure("TLabelframe.Label", background=T["bg"], foreground=T["accent"], font=f_bold)
    st.configure("Section.TLabel", background=T["bg"], foreground=T["accent"], font=f_bold)
    st.configure("TCheckbutton", background=T["panel"])
    st.map("TCheckbutton", background=[("active", T["panel"])],
           indicatorcolor=[("selected", T["accent"]), ("!selected", "white")])
    st.configure("TRadiobutton", background=T["bg"])
    st.map("TRadiobutton", background=[("active", T["bg"])],
           indicatorcolor=[("selected", T["accent"]), ("!selected", "white")])
    st.configure("Bar.TCheckbutton", background=T["bg"])
    st.map("Bar.TCheckbutton", background=[("active", T["bg"])])
    st.configure("TEntry", fieldbackground="white", bordercolor=T["line"], padding=3)
    st.map("TEntry", fieldbackground=[("disabled", T["bg"])], foreground=[("disabled", T["muted"])])
    st.configure("TCombobox", fieldbackground="white", padding=2, arrowcolor=T["ink"])
    st.map("TCombobox", fieldbackground=[("readonly", "white"), ("disabled", T["bg"])],
           selectbackground=[("readonly", "white")], selectforeground=[("readonly", T["ink"])])
    st.configure("TButton", background="#DDE2E6", padding=(10, 4))
    st.map("TButton", background=[("active", "#CFD6DC"), ("pressed", "#C3CAD1")])
    st.configure("Accent.TButton", background=T["accent"], foreground="white", font=f_bold, padding=(18, 6),
                 bordercolor=T["accent"])
    st.map("Accent.TButton", background=[("active", T["accent_hi"]), ("pressed", T["accent"])],
           foreground=[("disabled", "#DDE6EA")])
    st.configure("Link.TButton", background=T["bg"], foreground=T["accent"], borderwidth=0, padding=(4, 0))
    st.map("Link.TButton", background=[("active", T["bg"])], foreground=[("active", T["accent_hi"])])
    st.configure("Treeview", background="white", fieldbackground="white", rowheight=22, bordercolor=T["line"])
    st.configure("Treeview.Heading", background="#DDE2E6", font=f_bold)
    st.map("Treeview", background=[("selected", T["accent"])], foreground=[("selected", "white")])
    return st


def draw_nameplate(cv, logo, title, subtitle):
    """Kopfbereich im Stil eines eloxierten Typenschilds."""
    T = THEME
    _b, _bold, f_title, _m = _ui_fonts()
    cv.delete("all")
    w = max(cv.winfo_width(), 300)
    h = int(cv["height"])
    x0, y0, x1, y1 = 10, 8, w - 10, h - 4
    # gebuerstetes Aluminium: feine Verlaufslinien
    hi = [int(T["plate_hi"][i:i + 2], 16) for i in (1, 3, 5)]
    lo = [int(T["plate_lo"][i:i + 2], 16) for i in (1, 3, 5)]
    for y in range(y0, y1):
        t = (y - y0) / float(max(1, y1 - y0))
        t = 0.5 - 0.5 * math.cos(t * math.pi)
        c = [int(hi[k] + (lo[k] - hi[k]) * t) + (3 if y % 3 == 0 else 0) for k in range(3)]
        cv.create_line(x0, y, x1, y, fill="#%02x%02x%02x" % tuple(min(255, v) for v in c))
    cv.create_rectangle(x0, y0, x1, y1, outline=T["plate_edge"])
    cv.create_line(x0 + 1, y0 + 1, x1 - 1, y0 + 1, fill="#3A4D66")
    # Nieten
    for rx, ry in ((x0 + 12, y0 + 12), (x1 - 12, y0 + 12), (x0 + 12, y1 - 12), (x1 - 12, y1 - 12)):
        cv.create_oval(rx - 4, ry - 4, rx + 4, ry + 4, fill="#26364A", outline="#3E5470")
        cv.create_oval(rx - 2, ry - 2, rx + 1, ry + 1, fill="#5D7590", outline="")
    tx = x0 + 32
    if logo is not None:
        cv.create_image(tx, (y0 + y1) // 2, image=logo, anchor="w")
        tx += logo.width() + 18
        cv.create_line(tx - 9, y0 + 16, tx - 9, y1 - 16, fill="#2F4157")
    # gelaserte Schrift auf schwarzem Eloxal: hell, wie eine echte Markierung
    ty = (y0 + y1) // 2 - 10
    cv.create_text(tx, ty, text=title, anchor="w", font=f_title, fill="#E8EAF0")
    cv.create_text(tx, ty + 24, text=subtitle, anchor="w", font=_b, fill="#9FB2C8")


class PreviewWindow(object):
    """Vorschau-Fenster: Quelle (EZD), Ziel (LightBurn-Nachbildung), ueberlagert."""

    COL_SRC = "#202020"
    COL_DST = "#d0021b"
    COL_HATCH = "#c8c8c8"
    BOXES = {"feld": ("#1e6fd9", (6, 4), "Galvo-Feld"), "vorlage": ("#2e7d32", None, "Vorlagen-Kasten"),
             "innen": ("#2e7d32", (4, 3), "Kasten mit Rand")}

    def __init__(self, master, files, make_converter, log):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.files = list(files)
        self.make_converter = make_converter
        self.log = log
        self.pv = None
        self.win = tk.Toplevel(master)
        self.win.title("Vorschau")
        self.win.geometry("1000x760")
        self.win.configure(bg=THEME["bg"])
        self.v_file = tk.StringVar(value=os.path.basename(self.files[0]))
        self.v_mode = tk.StringVar(value="beide")
        self.v_hatch = tk.BooleanVar(value=False)
        self.v_boxes = tk.BooleanVar(value=True)

        bar = ttk.Frame(self.win, padding=(10, 8))
        bar.pack(fill="x")
        if len(self.files) > 1:
            cb = ttk.Combobox(bar, textvariable=self.v_file, state="readonly", width=34,
                              values=[os.path.basename(f) for f in self.files])
            cb.pack(side="left", padx=(0, 8))
            cb.bind("<<ComboboxSelected>>", lambda e: self.load())
        for val, txt in (("beide", "Ueberlagert"), ("quelle", "Quelle"), ("ziel", "Ziel")):
            ttk.Radiobutton(bar, text=txt, value=val, variable=self.v_mode, command=self.apply_vis).pack(
                side="left", padx=(0, 6))
        ttk.Checkbutton(bar, text="Schraffur", variable=self.v_hatch, command=self.apply_vis,
                        style="Bar.TCheckbutton").pack(side="left", padx=(12, 6))
        ttk.Checkbutton(bar, text="Rahmen", variable=self.v_boxes, command=self.apply_vis,
                        style="Bar.TCheckbutton").pack(side="left")
        ttk.Button(bar, text="Als PNG ...", command=self.png).pack(side="right")
        ttk.Button(bar, text="Aktualisieren", command=self.load).pack(side="right")
        ttk.Button(bar, text="Gesamtansicht", command=self.redraw).pack(side="right", padx=6)

        self.cv = tk.Canvas(self.win, bg="white", highlightthickness=1, highlightbackground=THEME["line"])
        self.cv.pack(fill="both", expand=True, padx=10)
        self.cv.bind("<Configure>", lambda e: self.redraw())
        self.cv.bind("<ButtonPress-1>", lambda e: self.cv.scan_mark(e.x, e.y))
        self.cv.bind("<B1-Motion>", lambda e: self.cv.scan_dragto(e.x, e.y, gain=1))
        self.cv.bind("<MouseWheel>", lambda e: self.zoom(e, 1.2 if e.delta > 0 else 1 / 1.2))
        self.cv.bind("<Button-4>", lambda e: self.zoom(e, 1.2))
        self.cv.bind("<Button-5>", lambda e: self.zoom(e, 1 / 1.2))

        bot = ttk.Frame(self.win, padding=(10, 6))
        bot.pack(fill="x")
        self.lbl_info = ttk.Label(bot, text="", anchor="w", justify="left", wraplength=960)
        self.lbl_info.pack(fill="x")
        leg = ttk.Frame(bot)
        leg.pack(fill="x")
        for col, txt in ((self.COL_SRC, "Quelle (EZD)"), (self.COL_DST, "Ziel (LightBurn-Nachbildung)"),
                         ("#1e6fd9", "Galvo-Feld"), ("#2e7d32", "Vorlagen-Kasten")):
            tk.Label(leg, text="\u25a0 " + txt, fg=col, bg=THEME["bg"]).pack(side="left", padx=(0, 12))
        tk.Label(bot, anchor="w", justify="left", fg="#8a5a00", bg=THEME["bg"], wraplength=960,
                 text="Hinweis: Die Zielansicht ist eine Nachbildung des LightBurn-Textsatzes mit der installierten "
                      "Schrift. Massgeblich ist die Darstellung in LightBurn. Mausrad = Zoom, linke Maustaste = "
                      "verschieben.").pack(fill="x")
        self.load()

    def current_file(self):
        name = self.v_file.get()
        for f in self.files:
            if os.path.basename(f) == name:
                return f
        return self.files[0]

    def load(self):
        src = self.current_file()
        self.win.config(cursor="watch")
        self.win.update_idletasks()
        try:
            conv = self.make_converter()
            ezd, st = conv.convert(src, None, preview=True)
            self.pv = conv.preview
            pv = self.pv
            info = "%s   |   Skalierung %.2f %%   |   Feld %s mm" % (os.path.basename(src), st["scale"] * 100,
                                                                   fmt(pv["field"]))
            if pv["extent"]:
                e = pv["extent"]
                info += "   |   Ziel-Inhalt %.1f x %.1f mm, X %.1f..%.1f  Y %.1f..%.1f" % (
                    e[2] - e[0], e[3] - e[1], e[0], e[2], e[1], e[3])
            if pv["missing"]:
                info += "   |   %d Text(e) ohne Darstellung (Schrift/fontTools fehlt)" % len(pv["missing"])
            if pv["unhatched"]:
                info += "   |   %d ungeschrafte(r) Text(e) in Quelle aus Schrift gezeichnet" % pv["unhatched"]
            self.lbl_info.configure(text=info)
            for w in ezd.warnings:
                self.log("     Vorschau-Hinweis: " + w)
        except Exception as ex:
            self.pv = None
            self.lbl_info.configure(text="Fehler: %s" % ex)
        self.win.config(cursor="")
        self.redraw()

    def _bounds(self):
        pts = []
        pv = self.pv
        for key in ("src", "dst"):
            for poly in pv[key]:
                pts.extend(poly)
        for _k, b in pv["boxes"]:
            if b:
                pts.extend([(b[0], b[1]), (b[2], b[3])])
        return bbox(pts) if pts else (0, 0, 100, 100)

    def redraw(self):
        cv = self.cv
        cv.delete("all")
        cv.xview_moveto(0)
        cv.yview_moveto(0)
        if not self.pv:
            return
        w, h = max(cv.winfo_width(), 50), max(cv.winfo_height(), 50)
        x0, y0, x1, y1 = self._bounds()
        sc = min((w - 40) / max(x1 - x0, 1e-6), (h - 40) / max(y1 - y0, 1e-6))
        ox = (w - (x1 - x0) * sc) / 2 - x0 * sc
        oy = (h + (y1 - y0) * sc) / 2 + y0 * sc

        def T(poly):
            out = []
            for x, y in poly:
                out.extend((x * sc + ox, oy - y * sc))
            return out

        for _k, b in self.pv["boxes"]:
            if not b:
                continue
            col, dash, _ = self.BOXES[_k]
            cv.create_rectangle(*T([(b[0], b[1]), (b[2], b[3])]), outline=col, dash=dash, width=2, tags=("box",))
        for poly in self.pv["hatch"]:
            if len(poly) > 1:
                cv.create_line(*T(poly), fill=self.COL_HATCH, tags=("hatch",))
        for poly in self.pv["src"]:
            if len(poly) > 1:
                cv.create_line(*T(poly), fill=self.COL_SRC, tags=("src",))
        for poly in self.pv["dst"]:
            if len(poly) > 1:
                cv.create_line(*T(poly), fill=self.COL_DST, tags=("dst",))
        self.apply_vis()

    def apply_vis(self):
        m = self.v_mode.get()
        show = {"src": m in ("beide", "quelle"), "dst": m in ("beide", "ziel"),
                "hatch": self.v_hatch.get() and m in ("beide", "quelle"), "box": self.v_boxes.get()}
        for tag, vis in show.items():
            self.cv.itemconfigure(tag, state="normal" if vis else "hidden")
        self.cv.tag_raise("dst")

    def zoom(self, e, f):
        x, y = self.cv.canvasx(e.x), self.cv.canvasy(e.y)
        self.cv.scale("all", x, y, f, f)

    def png(self):
        from tkinter import filedialog, messagebox

        if not self.pv:
            return
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            messagebox.showinfo("PNG", "Fuer den PNG-Export wird Pillow benoetigt:\npython -m pip install pillow")
            return
        dst = filedialog.asksaveasfilename(
            parent=self.win, defaultextension=".png", filetypes=[("PNG", "*.png")],
            initialfile=os.path.splitext(os.path.basename(self.current_file()))[0] + "_vorschau.png")
        if not dst:
            return
        x0, y0, x1, y1 = self._bounds()
        sc = min(20.0, 3000.0 / max(x1 - x0, y1 - y0, 1e-6))
        pad = 20
        W_, H_ = int((x1 - x0) * sc) + 2 * pad, int((y1 - y0) * sc) + 2 * pad
        img = Image.new("RGB", (W_, H_), "white")
        dr = ImageDraw.Draw(img)

        def T(poly):
            return [((x - x0) * sc + pad, H_ - pad - (y - y0) * sc) for x, y in poly]

        m = self.v_mode.get()
        if self.v_boxes.get():
            for k_, b in self.pv["boxes"]:
                if b:
                    dr.rectangle(T([(b[0], b[3]), (b[2], b[1])]), outline=self.BOXES[k_][0], width=2)
        if self.v_hatch.get() and m in ("beide", "quelle"):
            for poly in self.pv["hatch"]:
                if len(poly) > 1:
                    dr.line(T(poly), fill=self.COL_HATCH)
        if m in ("beide", "quelle"):
            for poly in self.pv["src"]:
                if len(poly) > 1:
                    dr.line(T(poly), fill=self.COL_SRC)
        if m in ("beide", "ziel"):
            for poly in self.pv["dst"]:
                if len(poly) > 1:
                    dr.line(T(poly), fill=self.COL_DST)
        img.save(dst)
        self.log("Vorschau gespeichert: %s" % dst)


def run_gui():
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    T = THEME
    f_body, f_bold, _f_title, f_mono = _ui_fonts()
    cfg = load_settings()
    win = tk.Tk()
    win.title("ezd2lbrn %s - EZCAD2 zu LightBurn" % VERSION)
    win.geometry(cfg.get("fenster", "820x860"))
    win.minsize(760, 720)
    apply_theme(win, ttk)
    files = []
    v_out = tk.StringVar(value=cfg.get("ziel", ""))
    v_field = tk.StringVar(value=cfg.get("feld", "110"))
    v_tpl = tk.StringVar(value=cfg.get("vorlage", ""))
    v_tpl_size = tk.BooleanVar(value=cfg.get("vorlage_groesse", False))
    v_tpl_margin = tk.StringVar(value=cfg.get("vorlage_rand", "0"))
    v_ls = tk.BooleanVar(value=cfg.get("zeichenabstand", True))
    v_fit = tk.BooleanVar(value=cfg.get("einpassen", False))
    v_margin = tk.StringVar(value=cfg.get("rand", "1"))
    v_scale = tk.StringVar(value=cfg.get("skalierung", "100"))
    v_src_font = tk.StringVar()
    v_dst_font = tk.StringVar()
    v_files = tk.StringVar(value="Noch keine Datei gewaehlt")
    font_map = dict(cfg.get("schriften", {}))  # Quelle -> Ziel (Originalschreibweise)
    last_in = [cfg.get("quelle_ordner", "")]

    sys_fonts = sorted({f for f in tkfont.families(win) if not f.startswith("@")}, key=str.lower)
    sys_lower = {f.lower() for f in sys_fonts}

    # ---- Kopf: Typenschild ----
    logo = load_logo(tk, win, 56)
    small_logo = load_logo(tk, win, 32, icon=True)
    if small_logo is not None:
        try:
            win.iconphoto(True, small_logo)
        except Exception:
            pass
    head = tk.Canvas(win, height=96, bg=T["bg"], highlightthickness=0)
    head.pack(fill="x", padx=6, pady=(6, 0))
    head.bind("<Configure>", lambda e: draw_nameplate(
        head, logo, "ezd2lbrn", "EZCAD2-Dateien in bearbeitbare LightBurn-Projekte umwandeln"))

    foot = ttk.Frame(win, padding=(14, 2, 14, 8))
    foot.pack(side="bottom", fill="x")
    body = ttk.Frame(win, padding=(14, 10, 14, 4))
    body.pack(fill="both", expand=True)

    def log(msg, tag=None):
        txt.configure(state="normal")
        if tag is None:
            tag = "err" if msg.startswith("FEHLER") else ("ok" if msg.startswith("OK") else (
                "hint" if "Hinweis" in msg else None))
        txt.insert("end", msg + "\n", tag)
        txt.see("end")
        txt.configure(state="disabled")
        win.update_idletasks()

    def store():
        cfg.update({
            "ziel": v_out.get(), "feld": v_field.get(), "vorlage": v_tpl.get(),
            "vorlage_groesse": v_tpl_size.get(), "vorlage_rand": v_tpl_margin.get(),
            "zeichenabstand": v_ls.get(), "einpassen": v_fit.get(), "rand": v_margin.get(),
            "skalierung": v_scale.get(), "schriften": font_map, "quelle_ordner": last_in[0],
            "fenster": win.geometry(),
        })
        save_settings(cfg)

    # ---- Dateien ----
    def pick_files():
        sel = filedialog.askopenfilenames(title="EZD-Dateien waehlen", initialdir=last_in[0] or None,
                                          filetypes=[("EZCAD2", "*.ezd"), ("Alle", "*.*")])
        if not sel:
            return
        files[:] = list(sel)
        last_in[0] = os.path.dirname(files[0])
        names = ", ".join(os.path.basename(x) for x in files)
        v_files.set(("%d Datei: %s" if len(files) == 1 else "%d Dateien: %s") % (len(files), names[:110]))
        if not v_out.get():
            v_out.set(os.path.dirname(files[0]))
        found = set()
        for f in files:
            try:
                found |= ezd_fonts(f)
            except Exception as ex:
                log("Schriften in %s nicht lesbar: %s" % (os.path.basename(f), ex))
        cb_src.configure(values=sorted(found | set(font_map), key=str.lower))
        for fn in sorted(found, key=str.lower):
            inst = fn.lower() in sys_lower
            ziel = font_map.get(fn)
            log("Schrift in EZD: %s (%s)%s" % (fn, "installiert" if inst else "nicht installiert, bitte ersetzen",
                                               (" -> wird ersetzt durch %s" % ziel) if ziel else ""),
                None if (inst or ziel) else "hint")
            if fn.lower() not in sys_lower and not v_src_font.get():
                v_src_font.set(fn)
        store()

    def pick_out():
        d = filedialog.askdirectory(title="Zielordner", initialdir=v_out.get() or last_in[0] or None)
        if d:
            v_out.set(d)
            store()

    def pick_tpl():
        cur = v_tpl.get()
        p = filedialog.askopenfilename(title="LightBurn-Vorlage", initialdir=os.path.dirname(cur) if cur else None,
                                       filetypes=[("LightBurn", "*.lbrn2"), ("Alle", "*.*")])
        if p:
            v_tpl.set(p)
            upd()
            store()

    def clear_tpl():
        v_tpl.set("")
        upd()
        store()

    # ---- Schriften ----
    def refresh_fonts():
        tree.delete(*tree.get_children())
        for k_, v in sorted(font_map.items(), key=lambda kv: kv[0].lower()):
            tree.insert("", "end", values=(k_, v))

    def add_font():
        a_, b_ = v_src_font.get().strip(), v_dst_font.get().strip()
        if not a_ or not b_:
            messagebox.showinfo("Schrift ersetzen", "Bitte Quelle und Ziel waehlen.")
            return
        for k_ in list(font_map):
            if k_.lower() == a_.lower():
                del font_map[k_]
        font_map[a_] = b_
        refresh_fonts()
        store()

    def del_font():
        for item in tree.selection():
            font_map.pop(tree.item(item, "values")[0], None)
        refresh_fonts()
        store()

    def on_tree(_e=None):
        sel = tree.selection()
        if sel:
            k_, v = tree.item(sel[0], "values")
            v_src_font.set(k_)
            v_dst_font.set(v)

    def upd(*_):
        has_tpl = bool(v_tpl.get())
        cb_tpl_size.state(["!disabled"] if has_tpl else ["disabled"])
        by_tpl = has_tpl and v_tpl_size.get()
        e_tpl_margin.state(["!disabled"] if by_tpl else ["disabled"])
        cb_fit.state(["disabled"] if by_tpl else ["!disabled"])
        e_scale.state(["disabled"] if (by_tpl or v_fit.get()) else ["!disabled"])
        e_margin.state(["!disabled"] if (v_fit.get() and not by_tpl) else ["disabled"])

    # ---- Konvertieren ----
    def make_converter():
        try:
            field = float(v_field.get().replace(",", "."))
            margin = float(v_margin.get().replace(",", "."))
            scale = float(v_scale.get().replace(",", "."))
            tpl_margin = float(v_tpl_margin.get().replace(",", ".") or 0)
        except ValueError:
            messagebox.showerror("Fehler", "Feldgroesse, Rand oder Skalierung ist keine Zahl.")
            return None
        if scale <= 0 or field <= 0:
            messagebox.showerror("Fehler", "Feldgroesse und Skalierung muessen groesser 0 sein.")
            return None
        tpl = v_tpl.get() or None
        if tpl and not os.path.isfile(tpl):
            messagebox.showerror("Fehler", "Vorlage nicht gefunden:\n%s" % tpl)
            return None
        store()
        fmap = {k_.lower(): v for k_, v in font_map.items()}
        return Converter(field, v_ls.get(), tpl, fmap, log, fit=v_fit.get(), margin=margin, scale_pct=scale,
                         size_from_template=bool(tpl and v_tpl_size.get()), tpl_margin=tpl_margin)

    def dst_for(src):
        out = v_out.get() or None
        if out:
            os.makedirs(out, exist_ok=True)
            return os.path.join(out, os.path.splitext(os.path.basename(src))[0] + ".lbrn2")
        return _target(src, None, 1)

    def convert_files(srcs):
        conv = make_converter()
        if conv is None:
            return
        ok = 0
        win.config(cursor="watch")
        win.update_idletasks()
        for src in srcs:
            dst = dst_for(src)
            try:
                ezd, st = conv.convert(src, dst)
                ok += 1
                log("OK   %s  ->  %s   (%d Texte, %d Pfade, Skalierung %.2f %%)"
                    % (os.path.basename(src), dst, st["text"], st["path"], st["scale"] * 100))
                for w in ezd.warnings:
                    log("     Hinweis: " + w)
            except Exception as ex:
                log("FEHLER  %s: %s" % (os.path.basename(src), ex))
        win.config(cursor="")
        log("Fertig: %d von %d konvertiert.\n" % (ok, len(srcs)))

    def go():
        if not files:
            messagebox.showwarning("Hinweis", "Bitte zuerst EZD-Dateien waehlen.")
            return
        convert_files(files)

    def preview():
        if not files:
            messagebox.showwarning("Hinweis", "Bitte zuerst EZD-Dateien waehlen.")
            return
        if make_converter() is None:
            return

        def mk():
            c_ = make_converter()
            if c_ is None:
                raise EzdError("Einstellungen ungueltig")
            return c_

        PreviewWindow(win, files, mk, log)

    def about():
        messagebox.showinfo(
            "Info",
            "ezd2lbrn %s\nEZCAD2 (.ezd) zu LightBurn (.lbrn2), offline.\n\n"
            "\u00a9 %s %s\n%s\n\nOpen Source unter der MIT-Lizenz: frei nutzbar,\n"
            "veraenderbar und weitergebbar, ohne Gewaehrleistung.\n\n"
            "Enthaelt Teile auf Basis von MeerK40t (MIT) und verwendet\n"
            "fontTools (MIT) und Pillow (MIT-CMU). Vollstaendige Lizenztexte:\n"
            "Schaltflaeche \"Lizenzen\" unten rechts.\n\n"
            "EZCAD ist eine Marke von Beijing JCZ Technology, LightBurn eine\n"
            "Marke von LightBurn Software LLC. ezd2lbrn ist unabhaengig und\n"
            "steht in keiner Verbindung zu diesen Unternehmen.\n\n"
            "Keine Gewaehrleistung: Laserparameter und Ergebnis vor dem ersten\n"
            "Lauf immer an Testmaterial pruefen.\n\n"
            "Einstellungen: %s" % (VERSION, APP_YEAR, APP_AUTHOR, APP_URL.split("//")[-1], settings_path()))

    # ---- Layout ----
    def section(title):
        """Abschnitt: Ueberschrift auf dem Fensterhintergrund, darunter eine helle Flaeche mit Rahmen."""
        ttk.Label(body, text=title, style="Section.TLabel").pack(anchor="w", pady=(2, 3))
        box = tk.Frame(body, bg=T["panel"], highlightthickness=1, highlightbackground=T["line"],
                       highlightcolor=T["line"], padx=10, pady=8)
        box.pack(fill="x", pady=(0, 10))
        return box

    s1 = section("Dateien")
    ttk.Button(s1, text="EZD-Dateien waehlen ...", command=pick_files, width=24).grid(row=0, column=0, sticky="w")
    ttk.Label(s1, textvariable=v_files, style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=10)
    ttk.Button(s1, text="Zielordner ...", command=pick_out, width=24).grid(row=1, column=0, sticky="w", pady=(6, 0))
    ttk.Entry(s1, textvariable=v_out).grid(row=1, column=1, sticky="we", padx=(10, 0), pady=(6, 0))
    s1.columnconfigure(1, weight=1)

    s2 = section("Vorlage (optional)")
    ttk.Button(s2, text="Vorlage ...", command=pick_tpl, width=24).grid(row=0, column=0, sticky="w")
    ttk.Entry(s2, textvariable=v_tpl).grid(row=0, column=1, sticky="we", padx=(10, 4))
    ttk.Button(s2, text="Entfernen", command=clear_tpl).grid(row=0, column=2)
    cb_tpl_size = ttk.Checkbutton(s2, text="Groesse und Lage aus Vorlage uebernehmen", variable=v_tpl_size,
                                  command=upd)
    cb_tpl_size.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
    rf = ttk.Frame(s2, style="Panel.TFrame")
    rf.grid(row=1, column=1, columnspan=2, sticky="e", pady=(6, 0))
    ttk.Label(rf, text="Rand zum Kasten (mm)", style="Panel.TLabel").pack(side="left", padx=(0, 6))
    e_tpl_margin = ttk.Entry(rf, textvariable=v_tpl_margin, width=6)
    e_tpl_margin.pack(side="left")
    ttk.Label(s2, text="Uebernimmt Geraet und Laserparameter (Layer-Nr. = EZCAD-Stift).",
              style="Muted.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
    s2.columnconfigure(1, weight=1)

    s3 = section("Feld und Groesse")
    ttk.Label(s3, text="Galvo-Feld (mm)", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Entry(s3, textvariable=v_field, width=7).grid(row=0, column=1, sticky="w", padx=(6, 30))
    ttk.Label(s3, text="Skalierung (%)", style="Panel.TLabel").grid(row=0, column=2, sticky="w", padx=(24, 0))
    e_scale = ttk.Entry(s3, textvariable=v_scale, width=7)
    e_scale.grid(row=0, column=3, sticky="w", padx=(6, 0))
    cb_fit = ttk.Checkbutton(s3, text="Stattdessen auf Feld einpassen", variable=v_fit, command=upd)
    cb_fit.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
    ttk.Label(s3, text="Rand (mm)", style="Panel.TLabel").grid(row=1, column=2, sticky="w", padx=(24, 0),
                                                              pady=(6, 0))
    e_margin = ttk.Entry(s3, textvariable=v_margin, width=7)
    e_margin.grid(row=1, column=3, sticky="w", padx=(6, 0), pady=(6, 0))
    ttk.Checkbutton(s3, text="Zeichenabstand an EZCAD angleichen", variable=v_ls).grid(
        row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

    s4 = section("Schriften ersetzen")
    ttk.Label(s4, text="Quelle (aus EZD)", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
    cb_src = ttk.Combobox(s4, textvariable=v_src_font, width=24, values=sorted(font_map, key=str.lower))
    cb_src.grid(row=0, column=1, sticky="w", padx=(6, 16))
    ttk.Label(s4, text="Ziel (installiert)", style="Panel.TLabel").grid(row=0, column=2, sticky="w")
    cb_dst = ttk.Combobox(s4, textvariable=v_dst_font, width=24, values=sys_fonts, state="readonly")
    cb_dst.grid(row=0, column=3, sticky="w", padx=(6, 8))
    ttk.Button(s4, text="Hinzufuegen", command=add_font).grid(row=0, column=4, sticky="e")
    tree = ttk.Treeview(s4, columns=("q", "z"), show="headings", height=3)
    tree.heading("q", text="Quelle (EZCAD)")
    tree.heading("z", text="Ziel (LightBurn)")
    tree.grid(row=1, column=0, columnspan=4, sticky="we", pady=(6, 0))
    tree.bind("<<TreeviewSelect>>", on_tree)
    ttk.Button(s4, text="Entfernen", command=del_font).grid(row=1, column=4, sticky="ne", pady=(6, 0))
    s4.columnconfigure(3, weight=1)

    act = ttk.Frame(body)
    act.pack(fill="x", pady=(2, 8))
    ttk.Button(act, text="Konvertieren", style="Accent.TButton", command=go).pack(side="left")
    ttk.Button(act, text="Vorschau ...", command=preview).pack(side="left", padx=8)

    logf = tk.Frame(body, bg=T["line"], bd=0)
    logf.pack(fill="both", expand=True)
    txt = scrolledtext.ScrolledText(logf, height=9, state="disabled", font=f_mono, bd=0, relief="flat",
                                    bg="white", fg=T["ink"], padx=8, pady=6, highlightthickness=0)
    txt.pack(fill="both", expand=True, padx=1, pady=1)
    txt.tag_configure("ok", foreground="#1B6E3A")
    txt.tag_configure("err", foreground="#B3261E")
    txt.tag_configure("hint", foreground="#8A5A00")

    ttk.Label(foot, text="\u00a9 %s %s, MIT-Lizenz" % (APP_YEAR, APP_AUTHOR), style="Foot.TLabel").pack(side="left")

    def open_web():
        import webbrowser
        webbrowser.open(APP_URL)

    ttk.Button(foot, text=APP_URL.split("//")[-1], style="Link.TButton", command=open_web,
               takefocus=False, cursor="hand2").pack(side="left", padx=(8, 0))
    ttk.Button(foot, text="Info", style="Link.TButton", command=about, takefocus=False).pack(side="right")
    ttk.Button(foot, text="Lizenzen", style="Link.TButton", command=lambda: show_licenses(win),
               takefocus=False).pack(side="right")
    ttk.Label(foot, text="Version %s" % VERSION, style="Foot.TLabel").pack(side="right", padx=8)

    refresh_fonts()
    upd()
    try:
        import fontTools  # noqa: F401
    except ImportError:
        log("Hinweis: fontTools nicht installiert - Schriftmetriken werden geschaetzt.\n"
            "         Installieren mit:  python -m pip install fonttools pillow")

    def on_close():
        store()
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_close)
    win.mainloop()


if __name__ == "__main__":  # (c) Christopher Schmick, iot-simply.com
    if len(sys.argv) == 2 and sys.argv[1] in ("--lizenzen", "--licenses"):
        sys.stdout.write(licenses_text())
        sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--icon-export":
        import base64
        with open(sys.argv[2], "wb") as fh:
            fh.write(base64.b64decode("".join(ICON_PNG_B64)))
        sys.exit(0)
    if len(sys.argv) > 1:
        sys.exit(run_cli(sys.argv[1:]))
    run_gui()
