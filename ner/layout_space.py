"""Page-wide reservations and conservative free-space expansion in writing axes."""

import pymupdf as fitz


MAX_GROWTH = 36.0
GAP = 2.0


def _key(rect):
    return tuple(round(value, 3) for value in rect)


class PageLayoutSpace:
    def __init__(self, page, blocks, table_regions=()):
        self.bounds = page.rect * page.derotation_matrix
        self.text = [fitz.Rect(span['bbox'])
                     for block in page.get_text('dict', flags=fitz.TEXTFLAGS_TEXT)['blocks']
                     if block['type'] == 0 for line in block['lines'] for span in line['spans']]
        self.images = [fitz.Rect(image['bbox']) for image in page.get_image_info()]
        self.drawings = page.get_drawings()
        self.blocks = blocks
        self.table_regions = [fitz.Rect(rect) for rect in table_regions]
        self.reserved = []

    def reserve(self, plan):
        self.reserved.append(fitz.Rect(plan.rect))

    def _column_limits(self, block, rect):
        """Split a visible gutter between nearby, multiline horizontal blocks."""
        left, right = self.bounds.x0, self.bounds.x1
        for peer in self.blocks:
            if (peer['id'] == block['id'] or peer.get('fixed_layout')
                    or len(peer['lines']) < 2 or tuple(peer['lines'][0]['dir']) != (1, 0)):
                continue
            box = fitz.Rect(peer.get('text_bbox', peer['bbox']))
            if box.y0 > rect.y1 + MAX_GROWTH or box.y1 < rect.y0 - MAX_GROWTH:
                continue
            if box.x0 >= rect.x1 + 12:
                right = min(right, (rect.x1 + box.x0) / 2)
            elif box.x1 <= rect.x0 - 12:
                left = max(left, (box.x1 + rect.x0) / 2)
        return left, right

    def candidates(self, block, rotate):
        original = fitz.Rect(block['bbox']) & self.bounds
        if block.get('fixed_layout'):
            return [original]
        # In local writing coordinates text progresses right, then wraps down.
        # Transform the same rule for 90/180/270-degree writing directions.
        matrix, inverse = fitz.Matrix(rotate), fitz.Matrix(-rotate)
        base, bounds = original * matrix, self.bounds * matrix
        owned = {_key(span['bbox']) for line in block['lines'] for span in line['spans']}
        obstacles = [box * matrix for box in self.text if _key(box) not in owned]
        obstacles.extend(box * matrix for box in self.images + self.reserved + self.table_regions)
        for drawing in self.drawings:
            box = fitz.Rect(drawing['rect']) * matrix
            if drawing['fill'] is not None and box.contains(base):
                # Existing shading can be used, but the text stays on that panel.
                bounds &= box
                continue
            padding = max(0.5, (drawing.get('width') or 0) / 2)
            obstacles.append(box + (-padding, -padding, padding, padding))
        # Existing overlap is uncertain space: keep the original frame and shrink.
        if any(base.intersects(box) for box in obstacles):
            return [original]
        margin_x = max(4, min(abs(base.x0 - bounds.x0), 40))
        margin_y = max(4, min(abs(base.y0 - bounds.y0), 40))
        right = max(base.x1, min(base.x1 + MAX_GROWTH, bounds.x1 - margin_x))
        bottom = max(base.y1, min(base.y1 + MAX_GROWTH, bounds.y1 - margin_y))
        left = bounds.x0 + margin_x
        if rotate == 0:
            column_left, column_right = self._column_limits(block, original)
            right = max(base.x1, min(right, column_right - GAP))
            left = max(left, column_left + GAP)
        centered = block.get('text_align') == 'center'
        if centered:
            # Keep a centered heading's center fixed when widening its frame.
            amount = max(0, min(MAX_GROWTH / 2, right - base.x1, base.x0 - left))
            right, left = base.x1 + amount, base.x0 - amount

        def widen(rect):
            end, start = right, left if centered else rect.x0
            for box in obstacles:
                if box.y0 < rect.y1 and rect.y0 < box.y1:
                    if box.x0 >= rect.x1:
                        end = min(end, max(rect.x1, box.x0 - GAP))
                    if centered and box.x1 <= rect.x0:
                        start = max(start, min(rect.x0, box.x1 + GAP))
            if centered:
                amount = max(0, min(end - base.x1, base.x0 - start))
                start, end = base.x0 - amount, base.x1 + amount
            return fitz.Rect(start, rect.y0, end, rect.y1)

        def deepen(rect):
            end = bottom
            for box in obstacles:
                if box.x0 < rect.x1 and rect.x0 < box.x1 and box.y0 >= rect.y1:
                    end = min(end, max(rect.y1, box.y0 - GAP))
            return fitz.Rect(rect.x0, rect.y0, rect.x1, end)

        horizontal, vertical = widen(base), deepen(base)
        local = [base, horizontal, vertical, deepen(horizontal), widen(vertical)]
        result, seen = [], set()
        for rect in local:
            if _key(rect) not in seen:
                seen.add(_key(rect))
                result.append(rect * inverse)
        return result
