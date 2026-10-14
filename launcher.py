import argparse
from pathlib import Path
import sys

from ner import CombinePDF, PaperExtract, TextraConnection


def _parser():
    parser = argparse.ArgumentParser(description='Translate English PDFs into Japanese.')
    parser.add_argument('command', choices=('translate', 'combine', 'info', 'help'), nargs='?', default='help')
    parser.add_argument('path', nargs='?', default='source.pdf')
    return parser


def help():
    _parser().print_help()


def translate(path='source.pdf'):
    with TextraConnection(path) as connection:
        connection.get_pdf_info()
        connection.print_pdf_info()
        connection.post_page()
        output = connection.insert()
        if connection.warnings:
            summary = connection.result_summary
            print(f"Warning: {summary['warning_count']} issue(s); "
                  f"{summary['translated_blocks']}/{summary['total_blocks']} blocks translated. "
                  'See the notices in the PDF error report.', file=sys.stderr)
        return output


def pdf_info(path='source.pdf'):
    # PDF inspection does not require translation credentials or model files.
    with PaperExtract(path) as document:
        document.extract_textinfo()
        count = sum(map(len, document.pages_info))
        print(f'Pages: {document.page_count}; translatable blocks: {count}')
        return document.meta_data


def combine(path='source.pdf'):
    original = Path(path).expanduser()
    translated = original.with_name(original.stem + '_new' + original.suffix)
    output = original.with_name(original.stem + '_all' + original.suffix)
    return CombinePDF(original, translated, output).combine()


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.command == 'help':
        help()
        return 0
    try:
        result = {'translate': translate, 'combine': combine, 'info': pdf_info}[args.command](args.path)
        if isinstance(result, str):
            print(result)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
