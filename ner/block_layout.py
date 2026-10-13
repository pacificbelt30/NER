"""Place validated protected spans in the translated paragraph's text flow."""

from dataclasses import dataclass
import html
from pathlib import Path

import pymupdf as fitz

from .errors import PDFError
from .text_blocks import TOKEN


@dataclass
class RichPlacement:
    rect: tuple
    text: str
    css: str
    scale_low: float
    rotate: int
    archive: object = None


def prepare_rich(source_page, block, result, font_path=None):
    page_rect = source_page.rect
    protected = {span.token: span for span in result.spans}
    parts, cursor = [], 0
    for match in TOKEN.finditer(result.canonical_text):
        parts.append(html.escape(result.canonical_text[cursor:match.start()]).replace('\n', '<br>'))
        span = protected[match.group()]
        content = html.escape(span.original).replace('\n', '<br>')
        if span.kind == 'code':
            content = '<code>' + content + '</code>'
        if span.href:
            content = '<a href="' + html.escape(span.href, quote=True) + '">' + content + '</a>'
        parts.append(content)
        cursor = match.end()
    parts.append(html.escape(result.canonical_text[cursor:]).replace('\n', '<br>'))
    size = block['fontsize']
    # Do not make tiny source footnotes smaller; ordinary text has an 8pt floor.
    minimum = min(size, 8)
    color = block['lines'][0]['spans'][0]['color']
    css = (f'body {{margin:0; font-family:sans-serif; font-size:{size}pt; line-height:1.1; color:#{color:06x};}} '
           'p {margin:0;} code {font-family:monospace;} a {color:#004c99;}')
    archive = None
    if font_path:
        path = Path(font_path)
        name = path.name.replace('\\', '\\\\').replace('"', '\\"')
        css += f' @font-face {{font-family:ner; src:url("{name}");}} body {{font-family:ner;}}'
        archive = fitz.Archive(str(path.parent))
    direction = tuple(block['lines'][0]['dir'])
    rotations = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}
    rotate = next((value for key, value in rotations.items()
                   if all(abs(a - b) < 0.001 for a, b in zip(direction, key))), None)
    if rotate is None:
        raise PDFError('Protected text block has an unsupported rotation angle.')
    plan = RichPlacement(block['bbox'], '<p>' + ''.join(parts) + '</p>', css, minimum / size, rotate, archive)
    def fits(rect):
        with fitz.open() as scratch:
            page = scratch.new_page(width=page_rect.width, height=page_rect.height)
            spare, _ = page.insert_htmlbox(rect, plan.text, css=css, scale_low=plan.scale_low,
                                          rotate=rotate, archive=archive)
            return spare >= 0
    if not fits(plan.rect):
        # Permit at most half an inch of horizontal growth into verified free space.
        # Code blocks and all other text, images, and vector strokes are obstacles.
        rect = fitz.Rect(plan.rect)
        expanded = fitz.Rect(rect)
        if rotate == 0:
            edge = min(rect.x1 + 36, page_rect.x1 - max(4, min(rect.x0, 40)))
            obstacles = [fitz.Rect(item['bbox']) for item in source_page.get_text(
                'dict', flags=fitz.TEXTFLAGS_TEXT)['blocks'] if item['type'] == 0
                and tuple(item['bbox']) != tuple(block.get('source_bbox', block['bbox']))]
            obstacles.extend(fitz.Rect(item['bbox']) for item in source_page.get_image_info())
            for drawing in source_page.get_drawings():
                box = fitz.Rect(drawing['rect'])
                if drawing['fill'] is not None and box.contains(rect):
                    continue  # The existing background is not a new obstacle.
                pad = max(0.5, (drawing.get('width') or 0) / 2)
                obstacles.append(box + (-pad, -pad, pad, pad))
            for obstacle in obstacles:
                if obstacle.y0 < rect.y1 and rect.y0 < obstacle.y1 and obstacle.x1 > rect.x1:
                    edge = min(edge, max(rect.x1, obstacle.x0 - 2))
            expanded.x1 = max(rect.x1, edge)
        if expanded == rect or not fits(expanded):
            raise PDFError(f"Page {block['page'] + 1}, block {block['id']}: protected translation "
                           'does not fit above the minimum font size.')
        plan.rect = tuple(expanded)
    return plan


def normalize_uri_links(page):
    """Remove empty Story link fragments and join adjacent runs on the same line."""
    links = [link for link in page.get_links() if link['kind'] == fitz.LINK_URI]
    groups = []
    for link in sorted(links, key=lambda row: (row['uri'], row['from'].y0, row['from'].x0)):
        rect = link['from']
        if rect.is_empty:
            continue
        previous = groups[-1] if groups else None
        if (previous and previous['uri'] == link['uri']
                and abs(previous['from'].y0 - rect.y0) < 0.5
                and abs(previous['from'].y1 - rect.y1) < 0.5
                and 0 <= rect.x0 - previous['from'].x1 <= 0.5):
            previous['from'] |= rect
        else:
            groups.append({'kind': fitz.LINK_URI, 'uri': link['uri'], 'from': fitz.Rect(rect)})
    for link in links:
        page.delete_link(link)
    for link in groups:
        page.insert_link(link)


class PreparedPage:
    def __init__(self, writer, rich):
        self.writer, self.rich = writer, rich

    def write_text(self, page, **kwargs):
        if not self.writer.text_rect.is_empty:
            self.writer.write_text(page, **kwargs)
        for plan in self.rich:
            spare, _ = page.insert_htmlbox(plan.rect, plan.text, css=plan.css, scale_low=plan.scale_low,
                                          rotate=plan.rotate, archive=plan.archive)
            if spare < 0:
                raise PDFError('Protected translation overflow during PDF placement.')
        page = page.parent.reload_page(page)
        normalize_uri_links(page)
