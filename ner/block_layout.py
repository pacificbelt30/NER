"""Place all translated PDF blocks, including literals, in HTML text flow."""

from dataclasses import dataclass
import html
import math
from pathlib import Path
import re

import pymupdf as fitz

from .errors import PDFError
from .layout_space import PageLayoutSpace
from .text_blocks import TOKEN


MIN_FONT_SIZE = 1.0


def _prose_html(text):
    # Single model line breaks are soft wraps; retain explicit paragraph breaks.
    return html.escape(re.sub(r'(?<!\n)\n(?!\n)', ' ', text)).replace('\n', '<br>')


@dataclass
class RichPlacement:
    rect: tuple
    text: str
    css: str
    scale_low: float
    rotate: int
    archive: object = None
    font_size: float = 0
    scale: float = 1


def prepare_rich(source_page, block, result, font_path=None, *, space=None):
    page_rect = source_page.rect * source_page.derotation_matrix
    protected = {span.token: span for span in result.spans}
    parts, cursor = [], 0
    for match in TOKEN.finditer(result.canonical_text):
        parts.append(_prose_html(result.canonical_text[cursor:match.start()]))
        span = protected[match.group()]
        content = html.escape(span.original).replace('\n', '<br>')
        if span.kind == 'code':
            content = '<code>' + content + '</code>'
        if span.href:
            content = '<a href="' + html.escape(span.href, quote=True) + '">' + content + '</a>'
        parts.append(content)
        cursor = match.end()
    parts.append(_prose_html(result.canonical_text[cursor:]))
    size = block['fontsize']
    if not math.isfinite(size) or size <= 0:
        raise PDFError('Text block has an invalid font size.')
    size = max(size, MIN_FONT_SIZE)
    minimum = MIN_FONT_SIZE
    first = block['lines'][0]['spans'][0]
    color = first['color']
    weight = 'bold' if first['flags'] & fitz.TEXT_FONT_BOLD else 'normal'
    style = 'italic' if first['flags'] & fitz.TEXT_FONT_ITALIC else 'normal'
    css = (f'body {{margin:0; font-family:sans-serif; font-size:{size}pt; line-height:1.1; '
           f'color:#{color:06x}; font-weight:{weight}; font-style:{style};}} '
           # CJK fallback glyph bounds can overhang the CSS line box. Vertical
           # padding keeps that ink inside the same obstacle-checked rectangle.
           f"p {{margin:0; padding:0.25em 0; text-align:{block.get('text_align', 'left')};}} "
           'code {font-family:monospace; font-size:1em;} a {color:#004c99;}')
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
        raise PDFError('Text block has an unsupported rotation angle.')
    rect = fitz.Rect(block['bbox'])
    if rect.is_empty or rect.is_infinite or not rect.is_valid:
        raise PDFError('Text block has an invalid rectangle.')
    plan = RichPlacement(block['bbox'], '<p>' + ''.join(parts) + '</p>', css, minimum / size,
                         rotate, archive, size)
    def fit(rect, scale_low):
        with fitz.open() as scratch:
            page = scratch.new_page(width=page_rect.width, height=page_rect.height)
            spare, scale = page.insert_htmlbox(rect, plan.text, css=css, scale_low=scale_low,
                                          rotate=rotate, archive=archive)
            return scale if spare >= 0 and scale >= scale_low - 1e-8 else None
    if space is None:
        space = PageLayoutSpace(source_page, [block])
    candidates = space.candidates(block, rotate)
    # Exhaust source-size placement and verified free space before shrinking.
    for candidate in candidates:
        if fit(candidate, 1) is not None:
            plan.rect = tuple(candidate)
            return plan
    # A containing rectangle cannot yield a worse fit: only test maximal frames.
    maximal = [rect for rect in candidates if not any(other != rect and other.contains(rect)
                                                    for other in candidates)]
    best = None
    for candidate in maximal:
        scale = fit(candidate, plan.scale_low)
        if scale is not None and (best is None or scale > best[1]):
            best = candidate, scale
    if best is None:
        raise PDFError(f"Page {block['page'] + 1}, block {block['id']}: translation "
                       f'does not fit at the minimum font size ({minimum:g}pt).')
    plan.rect, plan.scale = tuple(best[0]), best[1]
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
        if self.writer is not None and not self.writer.text_rect.is_empty:
            self.writer.write_text(page, **kwargs)
        for plan in self.rich:
            spare, scale = page.insert_htmlbox(plan.rect, plan.text, css=plan.css, scale_low=plan.scale_low,
                                          rotate=plan.rotate, archive=plan.archive)
            if spare < 0 or scale < plan.scale_low - 1e-8:
                raise PDFError('Translation overflow during PDF placement.')
        page = page.parent.reload_page(page)
        normalize_uri_links(page)
