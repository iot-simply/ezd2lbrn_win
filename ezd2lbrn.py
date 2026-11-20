#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ezd2lbrn - EZCAD2 (.ezd) -> LightBurn (.lbrn2) Konverter, offline.

Texte werden als echte LightBurn-Textobjekte geschrieben (bearbeitbar),
Position, Höhe, Fett, Ausrichtung, Zeilen- und Zeichenabstand werden aus der
EZD-Datei übernommen. Schraffuren werden zu Fill-Layern (Scan), Konturen zu
Line-Layern (Cut). EZCAD-Stifte werden zu LightBurn-Layern.

Aufruf:
    python ezd2lbrn.py                  -> GUI
    python ezd2lbrn.py datei.ezd [...]  -> Kommandozeile (siehe --help)

Copyright (c) 2026 Christopher Schmick, iot-simply.com
Open Source unter der MIT-Lizenz (Lizenztext: Schaltfläche "Lizenzen" im
Programm oder  python ezd2lbrn.py --lizenzen). Ohne Gewährleistung.

EZCAD ist eine Marke von Beijing JCZ Technology, LightBurn eine Marke von
LightBurn Software LLC. ezd2lbrn ist ein unabhängiges Projekt und steht in
keiner Verbindung zu diesen Unternehmen, es wird von ihnen weder unterstützt
noch gebilligt. Die Namen werden nur genannt, um die Kompatibilität zu
beschreiben.

Der EZD-Leseteil (Huffman-Dekompression, Objektstruktur, Feldbelegung) basiert
auf dem EZD-Parser von MeerK40t (https://github.com/meerk40t/meerk40t),
Copyright (c) 2021 meerk40t, MIT-Lizenz - vollständiger Lizenztext im Programm
(Schaltfläche "Lizenzen", Konstante LICENSES_Z_B64, Aufruf --lizenzen).
Er wurde für ezd2lbrn neu geschrieben und erweitert.
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

VERSION = "1.8.1"
APP_AUTHOR = "Christopher Schmick"
APP_BRAND = "iot-simply"
APP_URL = "https://iot-simply.com"
APP_YEAR = "2026"


def ausgeben(text, fehler=False):
    """Schreibt auf die Konsole, auch wenn es gar keine gibt.

    In der mit --noconsole gebauten .exe setzt PyInstaller sys.stdout und
    sys.stderr auf None. Ein direkter Zugriff löst dort eine Ausnahme aus, für
    die der Bootloader ein Fehlerfenster zeigt - auf einem Bauserver wartet
    darauf niemand. Deshalb läuft jede Ausgabe über diese Funktion.
    """
    strom = sys.stderr if fehler else sys.stdout
    try:
        if strom is not None:
            strom.write(text)
            strom.flush()
    except Exception:
        pass

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
        raise EzdError("Ungültige Strukturgröße %d" % count)
    out = []
    for _ in range(count):
        raw = f.read(4)
        if len(raw) != 4:
            raise EzdError("Unerwartetes Dateiende")
        (ln,) = struct.unpack("<i", raw)
        if ln < 0 or ln > max_size:
            raise EzdError("Ungültige Feldlänge %d" % ln)
        b = f.read(ln)
        if len(b) != ln:
            raise EzdError("Unerwartetes Dateiende")
        out.append(b)
    return out


def _v(b, kind=None):
    """Feld nach Länge/Typ interpretieren."""
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
    # Lookup-Tabelle für maxlen Bits
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
                raise EzdError("Ungültige Kinderanzahl")
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
            raise EzdError("Ungültige Kurve")
        self.closed = bool(closed)
        self.segments = []
        for _ in range(count):
            _u1, ctype, _u2, _u3 = struct.unpack("<BB2H", f.read(6))
            if ctype == 0:
                f.read(40)
                continue
            (n,) = struct.unpack("<i", f.read(4))
            if n < 0 or n > 10000000:
                raise EzdError("Ungültige Punktanzahl")
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
            raise EzdError("Ungültiges Bild")
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
            raise EzdError("Ungültiger Text")
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
        ezd.warn("Unbekannter Objekttyp 0x%X bei Offset %d - überspringe" % (t, start))
        return _resync(f, ezd, start + 1)
    try:
        return cls(f, ezd)
    except Exception as ex:
        if strict:
            raise
        ezd.warn("Objekt Typ 0x%X nicht lesbar (%s) - überspringe" % (t, ex))
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
        """Punkte auf der Kontur (Ecken + Zwischenpunkte der Beziers) für Begrenzungsrahmen."""
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
            ezd.warn("Rechteck mit abgerundeten Ecken wurde mit eckigen Ecken übernommen")
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
            ezd.warn("Ellipsenbogen wurde als volle Ellipse übernommen")
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
#  Schriften (für Zeilenabstand / Zeichenabstand)
# ---------------------------------------------------------------------------

ARIAL_K = (1854 + 434) / 2048.0  # (winAscent + winDescent) / unitsPerEm
ARIAL_AVG = {False: 904 / 2048.0, True: 983 / 2048.0}  # OS/2 xAvgCharWidth Arial / Arial Bold


class FontMetrics:
    """Liest Metriken einer installierten Schrift (benötigt fontTools)."""

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
        """Breite der sichtbaren Glyphen bei Schriftgröße size (ohne Kerning)."""
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
    """Kontur als Punktfolge (Beziers angenähert)."""
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
    """Zeichendaten für die Vorschau: Quelle, Ziel, Schraffur, Rahmen."""
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
        self.pv_paths = []  # Zielpfade für die Vorschau

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
                    ezd.warn("Vorlage enthält Texte ohne Kurvenkopie (nie in LightBurn gespeichert) - "
                             "Größe aus Vorlage nur aus den übrigen Objekten bestimmt")
            except Exception as ex:
                ezd.warn("Größe aus Vorlage nicht lesbar: %s" % ex)
                tb = None
            if not tb:
                ezd.warn("Vorlage hat keinen auswertbaren Inhalt - Größe nicht übernommen")
        self.tpl_outer = tb
        if tb and self.tpl_margin > 0:
            m = self.tpl_margin
            if tb[2] - tb[0] > 2 * m and tb[3] - tb[1] > 2 * m:
                tb = (tb[0] + m, tb[1] + m, tb[2] - m, tb[3] - m)
            else:
                ezd.warn("Rand %s mm ist größer als der halbe Vorlagen-Kasten - ohne Rand eingepasst" % fmt(m))
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
                ezd.warn("Design (%.1f x %.1f mm) ragt über das Galvo-Feld von %s mm hinaus - Feldgröße prüfen "
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
                    raise EzdError("Mehr als 30 Layer nötig - LightBurn unterstützt nur 30")
                L.index = free.pop(0)

    # ---- Baum durchlaufen ----
    def _walk(self, objs, out):
        i = 0
        while i < len(objs):
            o = objs[i]
            nxt = objs[i + 1] if i + 1 < len(objs) else None
            if o.hidden:
                self.ezd.warn("Ausgeblendete EZCAD-Objekte wurden nicht übernommen")
                i += 1
                continue
            if (o.array_x or 1) > 1 or (o.array_y or 1) > 1:
                self.ezd.warn("EZCAD-Arrays wurden nur als Einzelobjekt übernommen")
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
                self.ezd.warn("Bitmaps werden nicht unterstützt und wurden weggelassen")
            elif o.kind == "control":
                self.ezd.warn("Steuerobjekte (Timer, Ein-/Ausgänge, Achsen) wurden weggelassen")
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
            self.ezd.warn("Älteres Schraffurformat: Linienabstand/Winkel als Standard (0.05 mm / 0 Grad) gesetzt")
        if self._has_text(h):
            # Schraffur, die Textobjekte direkt enthält
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
                ezd.warn("Textobjekt '%s' ist kein normaler Text (z.B. Barcode) - als Kontur übernommen" % text[:30])
                self._emit_hatch(hatch, out)
                return

        # Zeilenabstand: EZCAD-Zeilenabstand = (Höhe + Zeilenlücke) * Skalierung
        pitch = (t.height + t.line_gap) * sy
        if n > 1:
            lns = (pitch / (k * H) - 1.0) * 100.0
            p_lb = pitch
        else:
            lns = 0.0
            p_lb = k * H
        b_last = t.ref[1] * sy  # Grundlinie der letzten Zeile (EZCAD-Bezugspunkt)
        ay = b_last + n * p_lb / 2.0  # Bezugspunkt für LightBurn Av=1

        # horizontale Ausrichtung (LightBurn richtet an der sichtbaren Glyphenkante aus)
        ah = t.align if t.align in (0, 1, 2) else 0
        if all_pts:
            bx0, _by0, bx1, _by1 = bbox(all_pts)
            ax = bx0 if ah == 0 else (bx1 if ah == 2 else (bx0 + bx1) / 2.0)
        else:
            ax = t.ref[0] * sy / sx

        # Rest-Zeichenabstand an EZCAD angleichen (HSpace, % der Schrifthöhe)
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
            ezd.warn("Variabler Text (Seriennummer/Datum/Datei) wurde als fester Text '%s' übernommen" % text[:30])

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
                    "an Testmaterial prüfen." % (os.path.basename(src), VERSION))
        else:
            note = ("Konvertiert aus EZCAD2-Datei %s mit ezd2lbrn %s. EZCAD-Mitte = %s/%s (Feld %s mm). "
                    "Laserparameter vor dem ersten Lauf an Testmaterial prüfen."
                    % (os.path.basename(src), VERSION, fmt(self.off), fmt(self.off), fmt(self.field)))
        if self.size_mode == "vorlage":
            note += (" Größe/Lage aus Vorlage %s übernommen (Rand %s mm): Kasten innen %.1f x %.1f mm, "
                     "Mitte %.2f/%.2f." % (os.path.basename(self.template), fmt(self.tpl_margin),
                                           self.tpl_box[2] - self.tpl_box[0],
                        self.tpl_box[3] - self.tpl_box[1], (self.tpl_box[0] + self.tpl_box[2]) / 2,
                        (self.tpl_box[1] + self.tpl_box[3]) / 2))
        if abs(self.s - 1.0) > 1e-9:
            note += (" Design skaliert auf %s %% (Texthöhen, Abstände und Konturen; Schraffur-Linienabstand "
                     "unverändert)." % fmt(round(self.s * 100, 2)))
        if self.ezd.warnings:
            note += " Hinweise: " + " | ".join(self.ezd.warnings)
        out.append('    <Notes ShowOnLoad="%d" Notes=%s/>' % (1 if self.ezd.warnings else 0, quoteattr(note)))
        out.append("</LightBurnProject>")
        return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
#  Oberfläche
# ---------------------------------------------------------------------------


# Vollständige Lizenztexte der Drittkomponenten (zlib-komprimiert, base64)
LICENSES_Z_B64 = (
    "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"
)


def licenses_text():
    import base64
    import zlib

    return zlib.decompress(base64.b64decode("".join(LICENSES_Z_B64))).decode("utf-8", "replace")


def lizenz_abschnitte(text):
    """Liefert [(Überschrift, Zeilennummer)] der Abschnitte im Lizenztext.

    Die Abschnitte sind von Linien aus 70 Gleichheitszeichen eingefasst; die
    Überschrift steht in der Zeile direkt nach der öffnenden Linie.
    """
    zeilen = text.split("\n")
    linien = [i for i, z in enumerate(zeilen) if z.strip() == "=" * 70]
    gefunden = []
    for k in range(0, len(linien) - 1, 2):
        nr = linien[k] + 1
        if nr < len(zeilen):
            name = zeilen[nr].strip()
            if name:
                gefunden.append((name, nr + 1))
    return gefunden


def show_licenses(master):
    """Fenster mit allen Lizenztexten (im Programm eingebaut)."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    T = THEME
    _b, _bold, _t, f_mono = _ui_fonts()
    text = licenses_text()
    w = tk.Toplevel(master)
    w.title("Lizenzen")
    w.geometry("820x660")
    w.configure(bg=T["bg"])

    kopf = ttk.Frame(w, padding=(12, 10, 12, 0))
    kopf.pack(fill="x")
    ttk.Label(kopf, text="Lizenz von ezd2lbrn und der verwendeten Drittkomponenten",
              style="Section.TLabel").pack(anchor="w")
    ttk.Label(kopf, text="EZCAD ist eine Marke von Beijing JCZ Technology, LightBurn eine Marke von LightBurn "
                         "Software LLC. ezd2lbrn ist unabhängig und steht in keiner Verbindung zu diesen "
                         "Unternehmen.", style="Foot.TLabel", wraplength=780, justify="left").pack(
        anchor="w", pady=(2, 0))

    t = scrolledtext.ScrolledText(w, font=f_mono, wrap="word", bd=0, relief="flat", padx=10, pady=8,
                                  highlightthickness=1, highlightbackground=T["line"],
                                  bg=T["log_bg"], fg=T["ink"], insertbackground=T["ink"])
    t.insert("1.0", text)
    t.configure(state="disabled")

    leiste = ttk.Frame(w, padding=(12, 8))
    leiste.pack(fill="x")
    abschnitte = lizenz_abschnitte(text)
    if abschnitte:
        ttk.Label(leiste, text="Abschnitt").pack(side="left", padx=(0, 6))
        v_abschnitt = tk.StringVar(value=abschnitte[0][0])
        cb = ttk.Combobox(leiste, textvariable=v_abschnitt, state="readonly", width=36,
                          values=[a for a, _z in abschnitte])
        cb.pack(side="left")

        def springen(_e=None):
            for name, zeile in abschnitte:
                if name == v_abschnitt.get():
                    t.yview("%d.0" % zeile)
                    return

        cb.bind("<<ComboboxSelected>>", springen)

    def speichern():
        ziel = filedialog.asksaveasfilename(
            parent=w, title="Lizenztexte speichern", defaultextension=".txt",
            initialfile="ezd2lbrn-%s-lizenzen.txt" % VERSION,
            filetypes=[("Textdatei", "*.txt"), ("Alle", "*.*")])
        if not ziel:
            return
        try:
            with open(ziel, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as ex:
            messagebox.showerror("Lizenzen", "Konnte nicht gespeichert werden:\n%s" % ex, parent=w)
            return
        messagebox.showinfo("Lizenzen", "Gespeichert:\n%s" % ziel, parent=w)

    ttk.Button(leiste, text="Schließen", command=w.destroy).pack(side="right")
    ttk.Button(leiste, text="Als Datei speichern", command=speichern).pack(side="right", padx=8)
    t.pack(fill="both", expand=True, padx=12, pady=(8, 0))
    w.bind("<Escape>", lambda e: w.destroy())
    titelleiste_faerben(w)
    return w


def einstellungsordner_oeffnen():
    """Öffnet den Ordner mit der Einstellungsdatei im Dateimanager."""
    ordner = os.path.dirname(settings_path())
    try:
        if sys.platform.startswith("win"):
            os.startfile(ordner)  # noqa: S606
        else:
            import subprocess
            subprocess.Popen(["xdg-open" if sys.platform.startswith("linux") else "open", ordner])
    except Exception as ex:
        ausgeben("Ordner ließ sich nicht öffnen: %s\n" % ex, fehler=True)


def info_zeigen(master, v_design=None, design_wechseln=None):
    """Info-Fenster: Typenschild, Zweck, Gefahrenhinweis, Copyright, Marken."""
    import tkinter as tk
    from tkinter import ttk

    T = THEME
    f_body, f_bold, _f_title, _f_mono = _ui_fonts()
    w = tk.Toplevel(master)
    w.title("Info")
    w.configure(bg=T["bg"])
    w.resizable(False, False)
    try:
        w.transient(master)
    except Exception:
        pass

    logo = load_logo(tk, w, 44)
    w._logo = logo  # Verweis halten, sonst raeumt Tk das Bild weg
    kopf = tk.Canvas(w, height=84, width=600, bg=T["bg"], highlightthickness=0)
    kopf.pack(fill="x", padx=10, pady=(10, 2))
    kopf.bind("<Configure>", lambda e: draw_nameplate(
        kopf, logo, "ezd2lbrn", "EZCAD2 (.ezd) nach LightBurn (.lbrn2)"))

    leib = ttk.Frame(w, padding=(16, 8, 16, 4))
    leib.pack(fill="both", expand=True)

    ttk.Label(leib, text="Version %s" % VERSION, style="Section.TLabel").pack(anchor="w")
    ttk.Label(leib, text="Wandelt EZCAD2-Projektdateien (.ezd) in LightBurn-Projekte (.lbrn2) um. Texte bleiben "
                         "bearbeitbare LightBurn-Textobjekte statt Kurven. Läuft vollständig offline, es werden "
                         "keine Daten übertragen.",
              wraplength=580, justify="left").pack(anchor="w", pady=(2, 10))

    # Gefahrenhinweis, abgesetzt
    kasten = tk.Frame(leib, bg=T["warn_bg"], highlightthickness=1, highlightbackground=T["warn_rand"],
                      highlightcolor=T["warn_rand"], padx=12, pady=10)
    kasten.pack(fill="x")
    tk.Label(kasten, text="Verwendung auf eigene Gefahr", bg=T["warn_bg"], fg=T["ink"], font=f_bold,
             anchor="w").pack(fill="x")
    tk.Label(kasten, bg=T["warn_bg"], fg=T["ink"], font=f_body, justify="left", wraplength=560, anchor="w",
             text="ezd2lbrn wird ohne jede Gewährleistung weitergegeben. Das Programm steuert keinen Laser, "
                  "die erzeugte Datei enthält jedoch Laserparameter aus der EZD-Datei oder aus Ihrer Vorlage.\n\n"
                  "Prüfen Sie Geometrie, Größe, Position, Fokus und sämtliche Laserparameter in LightBurn und "
                  "erproben Sie jede Umwandlung an Testmaterial, bevor Sie ein Werkstück bearbeiten. Für Schäden "
                  "an Werkstück, Maschine, Optik, Augen oder Personen wird keine Haftung übernommen. Die "
                  "Sicherheitsregeln für den Betrieb Ihres Lasers gelten unverändert.").pack(fill="x", pady=(4, 0))

    ttk.Label(leib, text="© %s %s" % (APP_YEAR, APP_AUTHOR), style="Section.TLabel").pack(anchor="w", pady=(12, 0))
    ttk.Label(leib, text="Open Source unter der MIT-Lizenz: frei nutzbar, veränderbar und weitergebbar, "
                         "solange Copyright- und Lizenzhinweis erhalten bleiben.",
              wraplength=580, justify="left").pack(anchor="w", pady=(2, 0))

    def web():
        import webbrowser
        webbrowser.open(APP_URL)

    ttk.Button(leib, text=APP_URL.split("//")[-1], style="Link.TButton", command=web,
               takefocus=False, cursor="hand2").pack(anchor="w", pady=(2, 10))

    ttk.Label(leib, text="Marken", style="Section.TLabel").pack(anchor="w")
    ttk.Label(leib, text="EZCAD ist eine Marke von Beijing JCZ Technology Co., Ltd., LightBurn eine Marke von "
                         "LightBurn Software, LLC. ezd2lbrn ist ein unabhängiges Programm, steht in keiner "
                         "Verbindung zu diesen Unternehmen und liefert keine Programmteile dieser Hersteller "
                         "mit. Einzelheiten im Fenster „Lizenzen“.",
              wraplength=580, justify="left").pack(anchor="w", pady=(2, 10))

    ttk.Label(leib, text="Ablage", style="Section.TLabel").pack(anchor="w")
    ttk.Label(leib, text="Einstellungen: %s" % settings_path(),
              wraplength=580, justify="left", style="Foot.TLabel").pack(anchor="w", pady=(2, 0))
    ttk.Label(leib, text="Eine logo.png neben dem Programm ersetzt das Logo im Kopfbereich.",
              wraplength=580, justify="left", style="Foot.TLabel").pack(anchor="w")

    if v_design is not None and design_wechseln is not None:
        ttk.Checkbutton(leib, text="Dunkles Design", variable=v_design, onvalue="dunkel", offvalue="hell",
                        command=design_wechseln, style="Bar.TCheckbutton").pack(anchor="w", pady=(10, 0))

    knoepfe = ttk.Frame(w, padding=(16, 8, 16, 12))
    knoepfe.pack(fill="x")
    ttk.Button(knoepfe, text="Einstellungsordner öffnen", command=einstellungsordner_oeffnen).pack(side="left")
    ttk.Button(knoepfe, text="Schließen", style="Accent.TButton", command=w.destroy).pack(side="right")
    ttk.Button(knoepfe, text="Lizenzen", command=lambda: show_licenses(w)).pack(side="right", padx=8)

    w.bind("<Escape>", lambda e: w.destroy())
    titelleiste_faerben(w)
    w.update_idletasks()
    try:
        x = master.winfo_rootx() + (master.winfo_width() - w.winfo_width()) // 2
        y = master.winfo_rooty() + max(0, (master.winfo_height() - w.winfo_height()) // 3)
        w.geometry("+%d+%d" % (max(0, x), max(0, y)))
    except Exception:
        pass
    return w


def run_cli(argv):
    ap = argparse.ArgumentParser(description="EZCAD2 .ezd -> LightBurn .lbrn2 (Texte bleiben bearbeitbar)")
    ap.add_argument("eingabe", nargs="+", help=".ezd-Datei(en)")
    ap.add_argument("-o", "--ausgabe", help="Ausgabedatei (bei einer Eingabe) oder Ordner")
    ap.add_argument("--feld", type=float, default=110.0, help="Galvo-Feldgröße in mm (Standard 110)")
    ap.add_argument("--einpassen", action="store_true",
                    help="Design gleichmäßig verkleinern, bis es ins Feld passt (Skalierung um die EZCAD-Mitte)")
    ap.add_argument("--rand", type=float, default=1.0, help="Rand zum Feldrand beim Einpassen in mm (Standard 1)")
    ap.add_argument("--skalierung", type=float, default=100.0, help="Feste Skalierung in %% (ohne --einpassen)")
    ap.add_argument("--vorlage-groesse", action="store_true",
                    help="Größe und Lage des Inhalts der Vorlage übernehmen (proportional eingepasst, mittig)")
    ap.add_argument("--vorlage-rand", type=float, default=0.0,
                    help="Abstand zum Vorlagen-Kasten in mm bei --vorlage-groesse (Standard 0)")
    ap.add_argument("--vorlage", help=".lbrn2-Vorlage für Gerät und Layer-Parameter (Layer-Nr. = EZCAD-Stift)")
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
            ausgeben("OK  %s -> %s  (%d Texte, %d Pfade, Skalierung %.2f %%)\n"
                     % (src, dst, st["text"], st["path"], st["scale"] * 100))
            for w in ezd.warnings:
                ausgeben("    Hinweis: " + w + "\n")
        except Exception as ex:
            rc = 1
            ausgeben("FEHLER  %s: %s\n" % (src, ex), fehler=True)
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

# iot-simply Logo (freigestellt, für dunklen Hintergrund) und Programmsymbol
LOGO_PNG_B64 = (
    "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"
    "RU5ErkJggg=="
)
ICON_PNG_B64 = (
    "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"
)

THEMEN = {
    "hell": {
        "bg": "#E8EBEE",       # kühles Hellgrau (Werkstattwand)
        "panel": "#F6F7F8",    # Arbeitsflächen
        "ink": "#1E2830",      # Graphit, Schrift
        "muted": "#5E6973",    # Nebentexte
        "line": "#C3CAD1",     # Linien, Rahmen
        "accent": "#1C74AE",   # iot-simply Blau, abgedunkelt für weiße Schrift
        "accent_hi": "#2687C6",
        "brand": "#5FC8FD",    # iot-simply Blau (Logo)
        "feld": "white",       # Eingabefelder, Listen
        "knopf": "#DDE2E6",
        "knopf_an": "#CFD6DC",
        "knopf_zu": "#C3CAD1",
        "log_bg": "white",
        "ok": "#1B6E3A",       # Meldungen im Log
        "fehler": "#B3261E",
        "hinweis": "#8A5A00",
        "warn": "#B3261E",     # Gefahrenhinweis in der Fußzeile
        "warn_bg": "#FFF3CD",  # Gefahrenkasten im Info-Fenster
        "warn_rand": "#E8A33D",
        "plate_hi": "#1B2839", # schwarz eloxiertes Typenschild, hell
        "plate_lo": "#0C1321", # Logo-Hintergrund
        "plate_edge": "#33465E",
    },
    "dunkel": {
        "bg": "#1C2126",
        "panel": "#252B31",
        "ink": "#E6EAEE",
        "muted": "#9AA5B1",
        "line": "#3A424B",
        "accent": "#2E8FD0",
        "accent_hi": "#4AA6E2",
        "brand": "#5FC8FD",
        "feld": "#2E353C",
        "knopf": "#333B43",
        "knopf_an": "#3E474F",
        "knopf_zu": "#2B3239",
        "log_bg": "#171B1F",
        "ok": "#63D18C",
        "fehler": "#FF8A80",
        "hinweis": "#E6B457",
        "warn": "#FF8A80",
        "warn_bg": "#3B2F12",
        "warn_rand": "#E8A33D",
        "plate_hi": "#1B2839",
        "plate_lo": "#0C1321",
        "plate_edge": "#33465E",
    },
}

# Aktives Design. Wird in sich geändert statt ersetzt, damit Verweise wie
# "T = THEME" in laufenden Fenstern gültig bleiben.
THEME = dict(THEMEN["hell"])


def ist_dunkel():
    """True, wenn gerade das dunkle Design aktiv ist."""
    return THEME.get("bg") == THEMEN["dunkel"]["bg"]


def titelleiste_faerben(fenster, dunkel=None):
    """Färbt die Fenster-Titelleiste unter Windows passend zum Design.

    Tk zeichnet die Titelleiste nicht selbst, das macht Windows. Ab Windows 10
    (1809) lässt sie sich über DwmSetWindowAttribute umstellen: Attribut 20,
    auf älteren Fassungen 19. Auf anderen Systemen passiert nichts.
    """
    if dunkel is None:
        dunkel = ist_dunkel()
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        fenster.update_idletasks()
        kennung = ctypes.windll.user32.GetParent(fenster.winfo_id()) or fenster.winfo_id()
        wert = ctypes.c_int(1 if dunkel else 0)
        for attribut in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    kennung, attribut, ctypes.byref(wert), ctypes.sizeof(wert)) == 0:
                break
        # Ohne Neuzeichnen wechselt die Farbe erst, wenn das Fenster bewegt wird.
        if fenster.winfo_viewable():
            fenster.withdraw()
            fenster.deiconify()
    except Exception:
        pass


def design_setzen(name):
    """Schaltet das aktive Design um und liefert den tatsächlich gesetzten Namen."""
    if name not in THEMEN:
        name = "hell"
    THEME.clear()
    THEME.update(THEMEN[name])
    return name


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
           indicatorcolor=[("selected", T["accent"]), ("!selected", T["feld"])])
    st.configure("TRadiobutton", background=T["bg"])
    st.map("TRadiobutton", background=[("active", T["bg"])],
           indicatorcolor=[("selected", T["accent"]), ("!selected", T["feld"])])
    st.configure("Bar.TCheckbutton", background=T["bg"])
    st.map("Bar.TCheckbutton", background=[("active", T["bg"])])
    st.configure("TEntry", fieldbackground=T["feld"], foreground=T["ink"], insertcolor=T["ink"],
                 bordercolor=T["line"], padding=3)
    st.map("TEntry", fieldbackground=[("disabled", T["bg"])], foreground=[("disabled", T["muted"])])
    st.configure("TCombobox", fieldbackground=T["feld"], foreground=T["ink"], padding=2, arrowcolor=T["ink"])
    st.map("TCombobox", fieldbackground=[("readonly", T["feld"]), ("disabled", T["bg"])],
           selectbackground=[("readonly", T["feld"])], selectforeground=[("readonly", T["ink"])])
    # Das Klappmenü der Comboboxen ist ein klassisches tk-Widget und folgt nur der Optionsdatenbank.
    win.option_add("*TCombobox*Listbox.background", T["feld"])
    win.option_add("*TCombobox*Listbox.foreground", T["ink"])
    win.option_add("*TCombobox*Listbox.selectBackground", T["accent"])
    win.option_add("*TCombobox*Listbox.selectForeground", "white")
    st.configure("TButton", background=T["knopf"], foreground=T["ink"], padding=(10, 4))
    st.map("TButton", background=[("active", T["knopf_an"]), ("pressed", T["knopf_zu"])])
    st.configure("Accent.TButton", background=T["accent"], foreground="white", font=f_bold, padding=(18, 6),
                 bordercolor=T["accent"])
    st.map("Accent.TButton", background=[("active", T["accent_hi"]), ("pressed", T["accent"])],
           foreground=[("disabled", "#DDE6EA")])
    st.configure("Link.TButton", background=T["bg"], foreground=T["accent"], borderwidth=0, padding=(4, 0))
    st.map("Link.TButton", background=[("active", T["bg"])], foreground=[("active", T["accent_hi"])])
    st.configure("Treeview", background=T["feld"], fieldbackground=T["feld"], foreground=T["ink"],
                 rowheight=22, bordercolor=T["line"])
    st.configure("Treeview.Heading", background=T["knopf"], foreground=T["ink"], font=f_bold)
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
    # gebürstetes Aluminium: feine Verlaufslinien
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
    """Vorschau-Fenster: Quelle (EZD), Ziel (LightBurn-Nachbildung), überlagert."""

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
        titelleiste_faerben(self.win)
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
        for val, txt in (("beide", "Überlagert"), ("quelle", "Quelle"), ("ziel", "Ziel")):
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
                      "Schrift. Maßgeblich ist die Darstellung in LightBurn. Mausrad = Zoom, linke Maustaste = "
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
            messagebox.showinfo("PNG", "Für den PNG-Export wird Pillow benötigt:\npython -m pip install pillow")
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
    win.minsize(760, 640)
    v_design = tk.StringVar(value=design_setzen(cfg.get("design", "hell")))
    apply_theme(win, ttk)
    titelleiste_faerben(win)
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
    v_files = tk.StringVar(value="Noch keine Datei gewählt")
    font_map = dict(cfg.get("schriften", {}))  # Quelle -> Ziel (Originalschreibweise)
    last_in = [cfg.get("quelle_ordner", "")]

    sys_fonts = sorted({f for f in tkfont.families(win) if not f.startswith("@")}, key=str.lower)
    sys_lower = {f.lower() for f in sys_fonts}

    # ---- Programmsymbol (das Typenschild steht im Info-Fenster) ----
    small_logo = load_logo(tk, win, 32, icon=True)
    if small_logo is not None:
        try:
            win.iconphoto(True, small_logo)
        except Exception:
            pass

    gefaerbt = []

    def faerben(widget, **zuordnung):
        """Merkt sich einfache tk-Widgets, damit der Designwechsel sie erreicht."""
        gefaerbt.append((widget, zuordnung))
        widget.configure(**{opt: THEME[schluessel] for opt, schluessel in zuordnung.items()})
        return widget

    foot = ttk.Frame(win, padding=(14, 2, 14, 8))
    foot.pack(side="bottom", fill="x")
    warnzeile = faerben(
        tk.Label(win, anchor="w", font=f_body,
                 text="Auf eigene Gefahr: Laserparameter, Position und Fokus vor dem ersten Lauf an "
                      "Testmaterial prüfen."),
        bg="bg", fg="warn")
    warnzeile.pack(side="bottom", fill="x", padx=14, pady=(0, 2))
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
            "fenster": win.geometry(), "design": v_design.get(),
        })
        save_settings(cfg)

    # ---- Dateien ----
    def pick_files():
        sel = filedialog.askopenfilenames(title="EZD-Dateien wählen", initialdir=last_in[0] or None,
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
            messagebox.showinfo("Schrift ersetzen", "Bitte Quelle und Ziel wählen.")
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
            messagebox.showerror("Fehler", "Feldgröße, Rand oder Skalierung ist keine Zahl.")
            return None
        if scale <= 0 or field <= 0:
            messagebox.showerror("Fehler", "Feldgröße und Skalierung müssen größer 0 sein.")
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
            messagebox.showwarning("Hinweis", "Bitte zuerst EZD-Dateien wählen.")
            return
        convert_files(files)

    def preview():
        if not files:
            messagebox.showwarning("Hinweis", "Bitte zuerst EZD-Dateien wählen.")
            return
        if make_converter() is None:
            return

        def mk():
            c_ = make_converter()
            if c_ is None:
                raise EzdError("Einstellungen ungültig")
            return c_

        PreviewWindow(win, files, mk, log)

    def design_wechseln():
        """Hell/Dunkel umschalten und alle Fenster nachfärben."""
        design_setzen(v_design.get())
        apply_theme(win, ttk)
        for widget, zuordnung in gefaerbt:
            try:
                widget.configure(**{opt: THEME[schluessel] for opt, schluessel in zuordnung.items()})
            except Exception:
                pass
        log_farben()
        for fenster in [win] + [k for k in win.winfo_children() if isinstance(k, tk.Toplevel)]:
            titelleiste_faerben(fenster)
        store()

    def info():
        info_zeigen(win, v_design, design_wechseln)

    # ---- Layout ----
    def section(title):
        """Abschnitt: Überschrift auf dem Fensterhintergrund, darunter eine helle Fläche mit Rahmen."""
        ttk.Label(body, text=title, style="Section.TLabel").pack(anchor="w", pady=(2, 3))
        box = faerben(tk.Frame(body, highlightthickness=1, padx=10, pady=8),
                      bg="panel", highlightbackground="line", highlightcolor="line")
        box.pack(fill="x", pady=(0, 10))
        return box

    s1 = section("Dateien")
    ttk.Button(s1, text="EZD-Dateien wählen ...", command=pick_files, width=24).grid(row=0, column=0, sticky="w")
    ttk.Label(s1, textvariable=v_files, style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=10)
    ttk.Button(s1, text="Zielordner ...", command=pick_out, width=24).grid(row=1, column=0, sticky="w", pady=(6, 0))
    ttk.Entry(s1, textvariable=v_out).grid(row=1, column=1, sticky="we", padx=(10, 0), pady=(6, 0))
    s1.columnconfigure(1, weight=1)

    s2 = section("Vorlage (optional)")
    ttk.Button(s2, text="Vorlage ...", command=pick_tpl, width=24).grid(row=0, column=0, sticky="w")
    ttk.Entry(s2, textvariable=v_tpl).grid(row=0, column=1, sticky="we", padx=(10, 4))
    ttk.Button(s2, text="Entfernen", command=clear_tpl).grid(row=0, column=2)
    cb_tpl_size = ttk.Checkbutton(s2, text="Größe und Lage aus Vorlage übernehmen", variable=v_tpl_size,
                                  command=upd)
    cb_tpl_size.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
    rf = ttk.Frame(s2, style="Panel.TFrame")
    rf.grid(row=1, column=1, columnspan=2, sticky="e", pady=(6, 0))
    ttk.Label(rf, text="Rand zum Kasten (mm)", style="Panel.TLabel").pack(side="left", padx=(0, 6))
    e_tpl_margin = ttk.Entry(rf, textvariable=v_tpl_margin, width=6)
    e_tpl_margin.pack(side="left")
    ttk.Label(s2, text="Übernimmt Gerät und Laserparameter (Layer-Nr. = EZCAD-Stift).",
              style="Muted.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
    s2.columnconfigure(1, weight=1)

    s3 = section("Feld und Größe")
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
    ttk.Button(s4, text="Hinzufügen", command=add_font).grid(row=0, column=4, sticky="e")
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

    logf = faerben(tk.Frame(body, bd=0), bg="line")
    logf.pack(fill="both", expand=True)
    txt = faerben(scrolledtext.ScrolledText(logf, height=9, state="disabled", font=f_mono, bd=0, relief="flat",
                                            padx=8, pady=6, highlightthickness=0),
                  bg="log_bg", fg="ink", insertbackground="ink")
    txt.pack(fill="both", expand=True, padx=1, pady=1)

    def log_farben():
        txt.tag_configure("ok", foreground=THEME["ok"])
        txt.tag_configure("err", foreground=THEME["fehler"])
        txt.tag_configure("hint", foreground=THEME["hinweis"])

    log_farben()

    ttk.Label(foot, text="ezd2lbrn %s" % VERSION, style="Foot.TLabel").pack(side="left")
    ttk.Button(foot, text="Info", style="Link.TButton", command=info, takefocus=False).pack(side="right")
    ttk.Button(foot, text="Lizenzen", style="Link.TButton", command=lambda: show_licenses(win),
               takefocus=False).pack(side="right", padx=(0, 10))

    refresh_fonts()
    upd()
    try:
        import fontTools  # noqa: F401
    except ImportError:
        log("Hinweis: fontTools nicht installiert - Schriftmetriken werden geschätzt.\n"
            "         Installieren mit:  python -m pip install fonttools pillow")

    def on_close():
        store()
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_close)
    win.mainloop()


def _test_objekte():
    """Baut einen kleinen EZCAD-Entwurf im Speicher: ein Rechteck und ein Text.

    Die Objekte werden ohne den Binärleser erzeugt, weil der Selbsttest ohne
    EZD-Datei auskommen muss. Alles danach - Geometrie, Schriftmetrik,
    Layerzuordnung und die Ausgabe als .lbrn2 - läuft wie bei einer echten
    Umwandlung.
    """
    stift = Pen.__new__(Pen)
    stift.__dict__.update(index=0, color=0, name="Selbsttest", passes=1, speed=1000.0, power=40.0,
                          frequency=30000, pulse_width=200, start_tc=100, end_tc=100, polygon_tc=50,
                          jump_speed=3000.0, wobble=0)

    kopf = dict(pen=0, type=0, state=0, hidden=False, label="", array_x=1, array_y=1,
                position=(0.0, 0.0), children=[])

    rechteck = Rect.__new__(Rect)
    rechteck.__dict__.update(kopf)
    rechteck.__dict__.update(p0=(-20.0, -10.0), p1=(20.0, 10.0), round=[0, 0, 0, 0], matrix=None)

    text = Text.__new__(Text)
    text.__dict__.update(kopf)
    text.__dict__.update(height=5.0, width=3.0, line_gap=1.0, ref=(-15.0, -3.0), align=0,
                         matrix=None, text="ezd2lbrn", font="Arial", bold=False, variable=False)

    ezd = EzdFile.__new__(EzdFile)
    ezd.__dict__.update(path="selbsttest.ezd", warnings=[], pens=[stift], fonts=["Arial"],
                        objects=[rechteck, text])
    return ezd


def selbsttest():
    """Prüft die gebaute Programmdatei: Oberfläche, Umwandlung, Lizenztexte.

    Rückgabe 0 = bestanden, sonst 1. Im Bau-Workflow läuft der Selbsttest gegen
    die fertige .exe: ein blosser --icon-export endet, bevor Tkinter überhaupt
    anläuft, und würde ein fehlendes Oberflächenpaket nicht bemerken.
    """
    fehler = []

    def pruefe(name, bedingung, hinweis=""):
        ausgeben("%-34s %s%s\n" % (name, "ok" if bedingung else "FEHLER", ("  " + hinweis) if hinweis else ""))
        if not bedingung:
            fehler.append(name)

    # 1. Umwandlung bis zur fertigen LightBurn-Datei
    xml = ""
    try:
        ezd = _test_objekte()
        conv = Converter(field_size=110.0)
        conv.ezd = ezd
        conv.s = 1.0
        conv.tx = conv.ty = conv.off
        conv.size_mode = None
        conv._reset()
        conv._walk(ezd.objects, conv.shapes)
        conv._assign_layer_indices()
        xml = conv._build_xml("selbsttest.ezd")
    except Exception as ex:
        ausgeben("Umwandlung fehlgeschlagen: %s\n" % ex, fehler=True)
    pruefe("Umwandlung lieferte XML", bool(xml))
    pruefe("Text blieb ein Textobjekt", 'Type="Text"' in xml)
    pruefe("Pfad wurde geschrieben", "<VertList" in xml or 'Type="Path"' in xml)
    pruefe("Laserparameter als CutSetting", "<CutSetting" in xml)
    try:
        ET.fromstring(xml)
        wohlgeformt = True
    except Exception as ex:
        wohlgeformt = False
        ausgeben("XML nicht lesbar: %s\n" % ex, fehler=True)
    pruefe("Ausgabe ist gültiges XML", wohlgeformt)

    # 2. Lizenztexte
    texte = ""
    try:
        texte = licenses_text()
    except Exception as ex:
        ausgeben("Lizenztexte nicht lesbar: %s\n" % ex, fehler=True)
    pruefe("Lizenztexte eingebettet", len(texte) > 50000, "%d Zeichen" % len(texte))
    pruefe("MIT-Lizenz enthalten", "MIT License" in texte)
    pruefe("Markenabschnitt enthalten", "MARKEN" in texte)
    abschnitte = lizenz_abschnitte(texte) if texte else []
    pruefe("Abschnitte auffindbar", len(abschnitte) >= 5, "%d gefunden" % len(abschnitte))

    # 3. Programmsymbol
    try:
        import base64
        symbol = base64.b64decode("".join(ICON_PNG_B64))
    except Exception:
        symbol = b""
    pruefe("Programmsymbol lesbar", symbol[:8] == b"\x89PNG\r\n\x1a\n")

    # 4. Oberfläche: beide Designs, Info- und Lizenzfenster wirklich aufbauen
    try:
        import tkinter as tk
        from tkinter import ttk

        wurzel = tk.Tk()
        wurzel.withdraw()
        for design in ("hell", "dunkel"):
            design_setzen(design)
            apply_theme(wurzel, ttk)
            wurzel.update_idletasks()
        design_setzen("hell")
        apply_theme(wurzel, ttk)

        leinwand = tk.Canvas(wurzel, height=84, width=600)
        draw_nameplate(leinwand, load_logo(tk, wurzel, 44), "ezd2lbrn", "Selbsttest")
        gezeichnet = len(leinwand.find_all()) > 10

        f1 = info_zeigen(wurzel)
        f2 = show_licenses(wurzel)
        wurzel.update_idletasks()
        fenster_ok = bool(f1.winfo_children())
        f1.destroy()
        if f2 is not None:
            f2.destroy()
        wurzel.destroy()
    except Exception as ex:
        gezeichnet = fenster_ok = False
        ausgeben("Oberfläche fehlgeschlagen: %s\n" % ex, fehler=True)
    pruefe("Typenschild gezeichnet", gezeichnet)
    pruefe("Info- und Lizenzfenster", fenster_ok)

    if fehler:
        ausgeben("\nSelbsttest FEHLGESCHLAGEN: %s\n" % ", ".join(fehler), fehler=True)
        return 1
    ausgeben("\nSelbsttest bestanden (ezd2lbrn %s).\n" % VERSION)
    return 0


if __name__ == "__main__":  # (c) Christopher Schmick, iot-simply.com
    if len(sys.argv) == 2 and sys.argv[1] in ("--lizenzen", "--licenses"):
        ausgeben(licenses_text())
        sys.exit(0)
    if len(sys.argv) == 2 and sys.argv[1] == "--selbsttest":
        sys.exit(selbsttest())
    if len(sys.argv) == 3 and sys.argv[1] == "--icon-export":
        import base64
        with open(sys.argv[2], "wb") as fh:
            fh.write(base64.b64decode("".join(ICON_PNG_B64)))
        sys.exit(0)
    if len(sys.argv) > 1:
        sys.exit(run_cli(sys.argv[1:]))
    run_gui()
