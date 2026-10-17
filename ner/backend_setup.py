"""Safe configuration diagnostics for the backend preparation screens.

Constructing a backend validates settings and local file availability only. It
must not start inference, download models, or contact a translation service.
"""

from .errors import ConfigurationError
from .translation import TranslationService

LABELS = {'INDEX': 'Index 2B', 'BERGAMOT': 'Firefox / Bergamot',
          'GOOGLE': 'Google翻訳', 'TEXTRA': 'TexTra'}


def require_real_credentials(config, name):
    if name != 'TEXTRA' or not isinstance(config.get(name), dict):
        return
    section = config[name]
    examples = {'NAME': {'YOUR_ACCOUNT_NAME'},
                'KEY': {'YOUR_API_KEY', 'keysample' * 5},
                'SECRET': {'YOUR_API_SECRET', 'secretsample' * 4}}
    if any(section.get(key) in values for key, values in examples.items()
           if isinstance(section.get(key), str)):
        raise ConfigurationError('TEXTRA sample credentials must be replaced.')


def _problem(name, exc):
    # Never return exception text: URLs, paths and credentials can occur in it.
    text = str(exc)
    if name == 'TEXTRA' and text.startswith('TEXTRA sample credentials'):
        return ('SAMPLE_CREDENTIALS', 'TexTraの認証情報がサンプルのままです。自分のアカウントの値に置き換えてください。',
                ['TEXTRA.NAME', 'TEXTRA.KEY', 'TEXTRA.SECRET'])
    if name == 'BERGAMOT' and isinstance(exc, FileNotFoundError):
        if text.startswith('Bergamot model configuration not found:'):
            return ('MODEL_CONFIG_NOT_FOUND', '英日モデルの設定ファイルが見つかりません。モデルを取得し、MODEL_CONFIGの参照先を確認してください。',
                    ['BERGAMOT.MODEL_CONFIG'])
        if text.startswith('Bergamot executable not found:'):
            return ('EXECUTABLE_NOT_FOUND', 'Bergamotの実行ファイルが見つからないか、実行権限がありません。ビルド・配置と設定を確認してください。',
                    ['BERGAMOT.WORKER_BINARY', 'BERGAMOT.BINARY'])
    if text == f'{name} must be a configuration mapping.':
        return ('INVALID_SECTION', f'{name}の設定は、項目名と値を持つYAMLのマッピングで記述してください。', [name])
    fields = ('BASE_URL', 'MODEL', 'API_KEY', 'MAX_TOKENS', 'MODEL_CONFIG', 'BINARY', 'WORKER_BINARY',
              'NUM_WORKERS', 'URL', 'NAME', 'KEY', 'SECRET', 'CHUNK_CHARS', 'TIMEOUT', 'REQUEST_INTERVAL')
    for field in fields:
        if text.startswith((f'{name}.{field} ', f'{field} ')):
            return ('INVALID_FIELD', f'{name}.{field}が未入力か、値の形式が正しくありません。下の設定例と手順を確認してください。',
                    [f'{name}.{field}'])
    for field in ('RETRIES', 'CACHE_SIZE', 'MAX_CHUNKS'):
        if text.startswith(field + ' '):
            return ('INVALID_FIELD', f'共通設定の{field}を確認してください。0以上の整数（MAX_CHUNKSは1以上）が必要です。', [field])
    return ('INVALID_CONFIGURATION', '設定値またはサーバー上のファイルを確認できません。下の準備手順を確認してください。', [name])


def backend_status(config, name, *, config_loaded=True):
    if not config_loaded:
        code, message, fields = ('CONFIGURATION_UNREADABLE',
            '翻訳設定を読み込めません。初回は下の接続設定を保存してください。既存ファイルがある場合は読み取り権限とYAMLの書式を確認してください。', [])
    elif name not in config and config.get('PRIORITY') != name:
        code, message, fields = ('MISSING_CONFIGURATION',
            f'{name}の設定がありません。下の準備手順を確認し、「接続・モデル設定」から保存してください。', [name])
    else:
        try:
            require_real_credentials(config, name)
            with TranslationService({**config, 'PRIORITY': name}):
                pass
        except (ConfigurationError, OSError) as exc:
            code, message, fields = _problem(name, exc)
        else:
            code, message, fields = ('CONFIGURED',
                '設定の形式と必要な実行ファイルの配置を確認しました。接続先の稼働・認証・実モデルの推論は未確認です。'
                if name == 'BERGAMOT' else '設定の形式を確認しました。接続先の稼働・認証・実モデルの推論は未確認です。', [])
    return {'id': name, 'label': LABELS[name], 'local': name in ('INDEX', 'BERGAMOT'),
            'configured': code == 'CONFIGURED',
            'setup': {'code': code, 'message': message, 'fields': fields,
                      'url': f'#settings/{name}', 'verification': 'configuration_only'}}
