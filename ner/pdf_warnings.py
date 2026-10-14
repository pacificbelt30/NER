"""Visible, portable reports for recoverable translation/placement failures."""

from dataclasses import asdict, dataclass
import json

import pymupdf as fitz
import regex


REPORT_PREFIX = 'ner-translation-report'
MESSAGES = {
    'TRANSLATION_FAILED': '翻訳に失敗したため、この箇所は原文を保持しました。',
    'PROTECTED_CONTENT_INVALID': 'URLやコードを変更する訳文が返されたため、原文を保持しました。',
    'LAYOUT_FAILED': '元のレイアウトに訳文を1pt以上で安全に配置できなかったため、原文を保持しました。訳文はこのレポートに掲載します。',
    'PAGE_RENDER_FAILED': 'ページの書き換えに失敗したため、このページ全体を原文に戻しました。',
    'EXTRACTION_FAILED': '本文の抽出に失敗したため、入力PDFを保持しました。',
    'OCR_REQUIRED': '翻訳可能な文字がありません。文字認識（OCR）を行ってから再試行してください。',
    'BACKEND_NOT_CONFIGURED': '翻訳エンジンを準備できなかったため、入力PDFを保持しました。設定を確認してください。',
    'FONT_FALLBACK': '指定フォントを使用できなかったため、標準の日本語フォントを使用しました。',
    'COMPARISON_FAILED': '比較PDFを作成できませんでした。翻訳PDFはダウンロードできます。',
}


@dataclass
class PDFWarning:
    code: str
    stage: str
    page: int | None = None  # One-based source page, never an appendix page.
    block_id: str | None = None
    bbox: tuple | None = None
    source: str = ''
    translation: str | None = None  # Only a complete, validated translation.

    @property
    def message(self):
        return MESSAGES[self.code]

    def view(self):
        return {'code': self.code, 'stage': self.stage, 'page': self.page,
                'block_id': self.block_id, 'message': self.message}


def _lines(text, font, width, size, advances):
    line, used = [], 0
    for cluster in regex.findall(r'\X', text.replace('\r\n', '\n').replace('\r', '\n')):
        if cluster == '\n':
            yield ''.join(line)
            line, used = [], 0
            continue
        if cluster == '\t':
            cluster = '    '
        if cluster not in advances:
            advances[cluster] = font.text_length(cluster, fontsize=size)
        advance = advances[cluster]
        if line and used + advance > width:
            yield ''.join(line)
            line, used = [], 0
        line.append(cluster)
        used += advance
    if line:
        yield ''.join(line)


def append_warning_report(document, warnings, summary, check_cancel=None):
    if not warnings:
        return 0
    original_pages = document.page_count
    font, size, advances = fitz.Font('japan'), 10, {}
    page = writer = None
    y, report_pages = 800, 0

    def flush():
        if writer is not None:
            writer.write_text(page)

    def write(text):
        nonlocal page, writer, y, report_pages
        for line in _lines(text, font, 531, size, advances):
            if y > 788:
                if check_cancel:
                    check_cancel()
                flush()
                page = document.new_page(width=595, height=842)
                writer = fitz.TextWriter(page.rect)
                report_pages += 1
                writer.append((32, 38), f'翻訳エラーレポート / {report_pages}', font=font, fontsize=12)
                page.insert_text((32, 821), f'NER translation report | {report_pages}', fontsize=8)
                y = 66
            if line:
                writer.append((32, y), line, font=font, fontsize=size)
            y += 15

    write(f"注意付き出力：{summary['translated_blocks']} / {summary['total_blocks']} ブロックを翻訳しました。\n"
          '失敗した箇所の原文は元のページに残しています。\n'
          '以下の訳文は翻訳に成功しましたが、元のページには配置できなかったものです。\n\n')
    for number, warning in enumerate(warnings, 1):
        location = f'原文 {warning.page} ページ' if warning.page else '文書全体'
        write(f'[{number}] {location} / {warning.block_id or warning.stage} / {warning.code}\n'
              f'{warning.message}\n')
        if warning.source:
            write('原文の先頭：' + warning.source[:240] + '\n')
        if warning.translation is not None:
            write('配置できなかった訳文（全文）：\n' + warning.translation + '\n')
        write('\n')
    flush()
    for number, warning in enumerate(warnings, 1):
        if check_cancel:
            check_cancel()
        page = document[(warning.page or 1) - 1]
        bounds = page.rect * page.derotation_matrix
        box = fitz.Rect(warning.bbox) & bounds if warning.bbox else None
        if box is not None and not box.is_empty and box.is_valid:
            annotation = page.add_rect_annot(box)
            annotation.set_colors(stroke=(0.9, 0.15, 0.1))
            annotation.set_border(width=0.7, dashes=[3, 2])
            annotation.set_info(title=f'NER [{number}]', content=warning.message)
            annotation.update()
            x = box.x0 - 21 if box.x0 >= 22 else min(bounds.x1 - 20, box.x1 + 2)
            y = min(max(bounds.y0 + 2, box.y0), bounds.y1 - 20)
        else:
            x, y = bounds.x1 - 22, bounds.y0 + 4
        note = page.add_text_annot((max(bounds.x0 + 2, x), y),
                                  f'[{number}] {warning.message}\n詳細は末尾の翻訳エラーレポートにあります。')
        note.set_info(title=f'NER [{number}]', subject=warning.code)
        note.update()
    report = {'schema': 1, 'original_pages': original_pages, 'report_pages': report_pages,
              'summary': summary | {'report_pages': report_pages},
              'warnings': [asdict(warning) | warning.view() for warning in warnings]}
    names = set(document.embfile_names())
    name, suffix = REPORT_PREFIX + '.json', 2
    while name in names:
        name = f'{REPORT_PREFIX}-{suffix}.json'
        suffix += 1
    document.embfile_add(name, json.dumps(report, ensure_ascii=False).encode('utf-8'),
                         filename=name, desc='NER translation warnings and validated unplaced text')
    return report_pages


def has_warning_appendix(document, original_pages):
    for name in document.embfile_names():
        if not name.startswith(REPORT_PREFIX):
            continue
        try:
            report = json.loads(document.embfile_get(name))
            if (report['schema'] == 1 and report['original_pages'] == original_pages
                    and report['report_pages'] > 0
                    and original_pages + report['report_pages'] == document.page_count):
                return True
        except (ValueError, KeyError, TypeError):
            continue
    return False
