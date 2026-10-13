"""Separate ruled-table cells before PDF prose grouping.

Character centers assign each glyph once, including merged cells and text spans
that cross columns. Geometry is copied while PyMuPDF's TableFinder is alive.
"""

from collections import defaultdict
import math

import pymupdf as fitz

from .errors import PDFError


def _bounds(items):
    rect = fitz.Rect()
    for item in items:
        rect |= fitz.Rect(item['bbox'])
    return tuple(rect)


def _line(line, spans):
    return dict(line, spans=spans, bbox=_bounds(spans))


def _span(span, chars):
    return dict(span, chars=chars, bbox=_bounds(chars), origin=chars[0]['origin'])


def _borderless_cells(page, source, ruled):
    """Infer only compact grids with at least three regularly spaced text rows.

    Long/wrapped paragraphs and uncertain two-row grids stay outside this rule.
    All rows must repeat the same separate line origins; ordinary prose wrapping
    and closely spaced newspaper columns must not become virtual tables.
    """
    lines = []
    for block in source:
        if block['type'] != 0:
            continue
        for line in block['lines']:
            if not line['spans'] or tuple(line['dir']) != (1, 0):
                continue
            box = fitz.Rect(line['bbox'])
            center = (box.tl + box.br) / 2
            if any(fitz.Rect(cell['bbox']).contains(center) for cell in ruled):
                continue
            text = ''.join(char['c'] for span in line['spans'] for char in span['chars'])
            lines.append({'bbox': box, 'baseline': line['spans'][0]['origin'][1],
                          'compact': len(text) <= 80 and len(text.split()) <= 10,
                          'size': max(span['size'] for span in line['spans'])})
    rows = []
    for line in sorted(lines, key=lambda item: (item['baseline'], item['bbox'].x0)):
        if not rows or abs(rows[-1][0]['baseline'] - line['baseline']) > 2:
            rows.append([])
        rows[-1].append(line)
    runs, run = [], []
    for row in rows:
        valid = (len(row) >= 2 and all(item['compact'] for item in row)
                 and all(right['bbox'].x0 - left['bbox'].x1 >= 12
                         for left, right in zip(row, row[1:])))
        if run and valid:
            pitch = row[0]['baseline'] - run[-1][0]['baseline']
            size = max(item['size'] for item in row + run[-1])
            valid = (len(row) == len(run[0])
                     and all(abs(a['bbox'].x0 - b['bbox'].x0) <= 2 for a, b in zip(row, run[0]))
                     and 1.7 * size <= pitch <= 4.5 * size
                     and (len(run) < 2 or abs(pitch - (run[-1][0]['baseline'] - run[-2][0]['baseline'])) <= 3))
        if not valid:
            if len(run) >= 3:
                runs.append(run)
            run = []
            # A differently aligned valid row can start another grid.
            valid = len(row) >= 2 and all(item['compact'] for item in row) and all(
                b['bbox'].x0 - a['bbox'].x1 >= 12 for a, b in zip(row, row[1:]))
        if valid:
            run.append(row)
    if len(run) >= 3:
        runs.append(run)
    result = []
    table_offset = max((cell['table'] for cell in ruled), default=-1) + 1
    page_rect = page.rect * page.derotation_matrix
    for index, run in enumerate(runs):
        pitch = (run[-1][0]['baseline'] - run[0][0]['baseline']) / (len(run) - 1)
        xs = [item['bbox'].x0 - 2 for item in run[0]]
        edge = min(page_rect.x1 - 4, xs[-1] + xs[-1] - xs[-2])
        if any(row[-1]['bbox'].x1 > edge - 2 for row in run):
            continue
        xs.append(edge)
        top = min(item['bbox'].y0 for item in run[0]) - 2
        table_rect = fitz.Rect(xs[0], top, xs[-1], top + pitch * len(run))
        member_ids = {id(item) for row in run for item in row}
        if any(id(item) not in member_ids and table_rect.contains(
                (item['bbox'].tl + item['bbox'].br) / 2) for item in lines):
            continue
        for row_index, row in enumerate(run):
            for col_index, _ in enumerate(row):
                result.append({'bbox': (xs[col_index], top + pitch * row_index,
                                        xs[col_index + 1], top + pitch * (row_index + 1)),
                               'table': table_offset + index, 'row': row_index, 'column': col_index,
                               'lines': [], 'order': len(source), 'strategy': 'aligned_text'})
    return result


def separate_table_cells(page, source):
    """Return ruled cells and conservative aligned-text cells before prose grouping."""
    paths = page.get_drawings()
    rotation = page.rotation
    try:
        # Ignore background-only rectangles, which can masquerade as a grid.
        # TableFinder uses the displayed page orientation, while raw text uses
        # unrotated coordinates. Normalize detection and restore the page always.
        if paths and rotation:
            page.set_rotation(0)
        finder = page.find_tables(strategy='lines_strict') if paths else None
    except Exception as exc:
        raise PDFError(f'Page {page.number + 1}: table detection failed.') from exc
    finally:
        if paths and rotation:
            page.set_rotation(rotation)
    cells, seen = [], set()
    for table_num, table in enumerate(finder.tables if finder else ()):
        if table.col_count < 2 or table.row_count < 2:
            continue
        for row_num, row in enumerate(table.rows):
            for col_num, bounds in enumerate(row.cells):
                if bounds is None or tuple(bounds) in seen:
                    continue
                seen.add(tuple(bounds))
                cells.append({'bbox': tuple(bounds), 'table': table_num,
                              'row': row_num, 'column': col_num, 'lines': [], 'order': len(source),
                              'strategy': 'lines_strict'})
    cells.extend(_borderless_cells(page, source, cells))
    if not cells:
        return source
    # Bound lookup cost to nearby cells, rather than scanning a whole large table
    # for every character. Shared edges use half-open containment below.
    buckets = defaultdict(list)
    tile = 64
    for index, cell in enumerate(cells):
        x0, y0, x1, y1 = cell['bbox']
        for x in range(math.floor(x0 / tile), math.ceil(x1 / tile)):
            for y in range(math.floor(y0 / tile), math.ceil(y1 / tile)):
                buckets[x, y].append(index)
    outside = []
    for order, block in enumerate(source):
        if block['type'] != 0:
            outside.append((order, block))
            continue
        remaining_lines = []
        for line in block['lines']:
            assigned_spans = defaultdict(list)
            for span in line['spans']:
                assigned_chars = defaultdict(list)
                for char in span['chars']:
                    rect = char['bbox']
                    x, y = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
                    candidates = buckets.get((math.floor(x / tile), math.floor(y / tile)), ())
                    target = next((index for index in candidates
                                   if cells[index]['bbox'][0] <= x < cells[index]['bbox'][2]
                                   and cells[index]['bbox'][1] <= y < cells[index]['bbox'][3]), -1)
                    assigned_chars[target].append(char)
                for target, chars in assigned_chars.items():
                    assigned_spans[target].append(_span(span, chars))
            for target, spans in assigned_spans.items():
                fragment = _line(line, spans)
                if target == -1:
                    remaining_lines.append(fragment)
                else:
                    cells[target]['lines'].append(fragment)
                    cells[target]['order'] = min(cells[target]['order'], order)
        if remaining_lines:
            outside.append((order, dict(block, lines=remaining_lines, bbox=_bounds(remaining_lines))))
    for cell in cells:
        if not cell['lines']:
            continue
        lines = sorted(cell['lines'], key=lambda line: (round(line['bbox'][1], 1), line['bbox'][0]))
        metadata = {key: value for key, value in cell.items() if key not in ('lines', 'order')}
        outside.append((cell['order'], {'type': 0, 'lines': lines, 'bbox': _bounds(lines),
                                       'table_cell': metadata}))
    return [block for _, block in sorted(outside, key=lambda item: item[0])]
