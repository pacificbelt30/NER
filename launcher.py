import argparse
import json
from pathlib import Path
import sys

from ner import CombinePDF, PaperExtract, TextraConnection
from ner.configuration import ConfigurationStore, FIELDS, effective_config
from ner.model_downloads import CATALOG, download_index
from ner.download_jobs import public_download_error


def _parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--config', dest='config_path', default=argparse.SUPPRESS,
                        help='Base YAML configuration (default: NER_CONFIG_PATH or config.yml).')
    common.add_argument('--storage', dest='storage_path', default=argparse.SUPPRESS,
                        help='Shared settings/models directory (default: NER_STORAGE_PATH or pdfstore).')
    parser = argparse.ArgumentParser(description='Translate English PDFs into Japanese.', parents=[common])
    commands = parser.add_subparsers(dest='command')
    for command in ('translate', 'combine', 'info'):
        child = commands.add_parser(command, parents=[common])
        child.add_argument('path', nargs='?', default='source.pdf')
    commands.add_parser('help')
    dl = commands.add_parser('dl', aliases=['download'], parents=[common], help='Download an official local model.')
    dl.add_argument('backend', type=str.lower, choices=['index', 'bergamot'])
    dl.add_argument('--format', dest='variant', choices=list(CATALOG), default='gguf-q4')
    dl.add_argument('--output', type=Path, help='Destination directory; default is inside the shared storage directory.')
    config = commands.add_parser('config', parents=[common], help='Show or save shared backend settings.')
    actions = config.add_subparsers(dest='config_command', required=True)
    show = actions.add_parser('show', parents=[common])
    show.add_argument('backend', nargs='?', type=str.upper, choices=list(FIELDS))
    save = actions.add_parser('set', parents=[common])
    save.add_argument('backend', type=str.upper, choices=list(FIELDS))
    save.add_argument('--values-file', help='JSON object with backend fields; use - for stdin.')
    save.add_argument('--default', action='store_true', help='Use this backend by default.')
    all_fields = {key: kind for fields in FIELDS.values() for key, kind in fields.items()}
    for key, kind in all_fields.items():
        save.add_argument('--' + key.lower().replace('_', '-'), dest=key,
                          type=int if kind == 'integer' else float if kind in ('number', 'nonnegative') else str)
    save.add_argument('--api-key-stdin', action='store_true', help='Read INDEX.API_KEY from one stdin line.')
    return parser


def help():
    _parser().print_help()


def translate(path='source.pdf', *, config=None):
    with TextraConnection(path, config=config) as connection:
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
    if args.command in (None, 'help'):
        help()
        return 0
    try:
        store = ConfigurationStore(getattr(args, 'config_path', None), getattr(args, 'storage_path', None))
        if args.command in ('dl', 'download'):
            if args.backend == 'index':
                last = [-1]
                def progress(value):
                    percent = int(value['downloaded_bytes'] * 100 / max(1, value['total_bytes']))
                    if percent != last[0]:
                        print(f"{value['stage']}: {percent}% {value.get('file') or ''}", file=sys.stderr)
                        last[0] = percent
                result = download_index(args.output or store.root / 'models' / 'index' / args.variant,
                                        args.variant, on_progress=progress)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                print('Model downloaded. Start a compatible model server, then save its API endpoint with config set index.', file=sys.stderr)
            else:
                from scripts.download_bergamot_model import download_model
                print(download_model(args.output or store.root / 'models' / 'bergamot' / 'en-ja'))
            return 0
        if args.command == 'config':
            if args.config_command == 'show':
                result = store.view()
                if args.backend:
                    result = result['backends'][args.backend]
            else:
                values = {}
                if args.values_file:
                    raw = sys.stdin.read() if args.values_file == '-' else Path(args.values_file).read_text(encoding='utf-8')
                    values = json.loads(raw)
                    if not isinstance(values, dict):
                        raise ValueError('Settings file must contain a JSON object.')
                for key in {key for fields in FIELDS.values() for key in fields}:
                    if getattr(args, key, None) is not None:
                        values[key] = getattr(args, key)
                if args.api_key_stdin:
                    if args.values_file == '-' or args.backend != 'INDEX':
                        raise ValueError('--api-key-stdin is for Index and cannot share stdin with --values-file -.')
                    values['API_KEY'] = sys.stdin.readline().rstrip('\r\n')
                result = store.save(args.backend, values, revision=store.revision(), make_default=args.default)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'translate':
            config = store.load() if getattr(args, 'config_path', None) or getattr(args, 'storage_path', None) else None
            result = translate(args.path, config=config)
        else:
            result = {'combine': combine, 'info': pdf_info}[args.command](args.path)
        if isinstance(result, str):
            print(result)
    except KeyboardInterrupt:
        print('Interrupted; partial Index downloads can be resumed.', file=sys.stderr)
        return 130
    except Exception as exc:
        if args.command in ('dl', 'download'):
            print('Error: ' + public_download_error(exc)['message'], file=sys.stderr)
        elif isinstance(exc, (OSError, ValueError, RuntimeError)):
            print(f'Error: {exc}', file=sys.stderr)
        else:
            raise
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
