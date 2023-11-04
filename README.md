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

### Config file
We need to prepare a configuration file.
This will contain information about the translation service, fonts, etc.
The name of the configuration file should be "config.yml". An example configuration is available as "config_sample.yml".
  
```yaml
PRIORITY: 'GOOGLE'
GOOGLE:
  URL: 'https://google.com'
TEXTRA:
  NAME: sample
  KEY: keysamplekeysamplekeysamplekeysamplekeysample
  SECRET: secretsamplesecretsamplesecretsamplesecretsample
  URL: 'https://mt-auto-minhon-mlt.ucri.jgn-x.jp/api/mt/generalNT_en_ja/'
FONT: '~/example.ttf'
```

### Config
You can use two translation services, [みんなの自動翻訳＠TexTra](https://mt-auto-minhon-mlt.ucri.jgn-x.jp) and [Google](https://translate.google.co.jp/) Translate.(DeepL is not supported)
If you use みんなの自動翻訳＠TexTra, you can choose from several endpoints.
For English-Japanese translation, the following two options are available:
- scienceNT: https://mt-auto-minhon-mlt.ucri.jgn-x.jp/api/mt/science_en_ja/
- generalNT: https://mt-auto-minhon-mlt.ucri.jgn-x.jp/api/mt/generalNT_en_ja/
  
The meaning of each setting will be added soon.
