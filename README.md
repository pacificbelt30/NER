# NER
NER is not Enough Readable.


- [REQUIREMENTS](#requirements)
- [INSTALL dependent python packages](#install-dependent-python-packages)
- [INSTALL nltk dataset/model](#install-nltk-dataset/model)
- [USAGE](#usage)
  - [List of COMMAND](#list-of-command)

## REQUIREMENTS
- python
- pipenv
- nltk dataset/model
- [みんなの自動翻訳＠TexTra®](https://mt-auto-minhon-mlt.ucri.jgn-x.jp/) or other translation services API account and key.

## INSTALL dependent python packages
```
pipenv install
```

## INSTALL nltk dataset/model
```python
python -m nltk.downloader popular
```
## USAGE
```sh
python launcher.py <COMMAND> [<PATH-of-origin-PDF-file>]
```

### List of COMMAND
| COMMAND | DESCRIPTION |
| ---- | ---- |
| help | Display help<br>`$ python launcher.py translate <PATH-of-origin-PDF-file>`  |
| translate | Translate the specified file. config.yml can be edited to change the translation service and fonts to be used. The output file name is the basename of the <PATH-of-origin-PDF-file> followed by "_new".<br><br> example:<br> &nbsp;&nbsp;&nbsp;&nbsp;test.pdf -> test_new.pdf<br> `$ python launcher.py translate <PATH-of-origin-PDF-file>` |
| combine | Combine the original and translated(suffix "_new") files. By displaying them as a spread, you can compare the original and the translated text.<br> `$ python launcher.py translate <PATH-of-origin-PDF-file>`|

