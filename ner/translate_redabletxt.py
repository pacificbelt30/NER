"""Translate separated text blocks without trying to open the text file as a PDF."""

from pathlib import Path

from .io_utils import atomic_output
from .translation import TranslationService
from .yml_load import config_load
from .text_blocks import TranslationBlock


class Block:
    split_marker = '------------------------------'

    def __init__(self, filename):
        self.txt = Path(filename).expanduser().read_text(encoding='utf-8')
        self.split_txt = self.split_array(self.txt)
        self.translate_api = TranslationService(config_load('./config.yml'))
        self.previous_result = None

    def split_array(self, text):
        return [block.strip() for block in text.split(self.split_marker) if block.strip()]

    def translate(self):
        self.previous_result = None
        results = [self.translate_api.translate_block(TranslationBlock(f'text-{index + 1}', text)).text
                   for index, text in enumerate(self.split_txt)]
        self.previous_result = ('\n' + self.split_marker + '\n').join(results)
        return self.previous_result

    def save(self, filename):
        if self.previous_result is None:
            raise RuntimeError('Translate the text before saving it.')
        with atomic_output(filename) as path:
            path.write_text(self.previous_result + '\n', encoding='utf-8')

    def close(self):
        self.translate_api.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
