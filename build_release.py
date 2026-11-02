"""Build a deterministic, versioned plugin ZIP and its public release manifest."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import zipfile

REPOSITORY = 'huangweif522-star/SuanliAI-Plugin'
PLUGIN_ID = 'SuanliAI-Plugin'
FILES = (
    'main.py', 'info.json', 'README.md', 'suanli_catalog.py', 'suanli_updates.py',
    'models_builtin.json', 'ui/index.html', 'ui/app.js', 'ui/model-search.js',
    'ui/models-builtin.js', 'ui/plugin-sdk.js',
)
ROOT = Path(__file__).resolve().parent


def build(output=None):
    plugin = ROOT / 'plugin'
    version = json.loads((plugin / 'info.json').read_text(encoding='utf-8'))['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid plugin version')
    folder = 'Suanli_Ai_v' + version
    filename = folder + '.zip'
    out = Path(output) if output else ROOT / 'dist'
    out.mkdir(parents=True, exist_ok=True)
    package = out / filename
    for name in FILES:
        if not (plugin / name).is_file():
            raise ValueError('Missing release file: ' + name)
        if name.endswith('.py'):
            ast.parse((plugin / name).read_text(encoding='utf-8'))
    models = json.loads((plugin / 'models_builtin.json').read_text(encoding='utf-8'))
    if len(models) != 111 or len(set(models)) != 111:
        raise ValueError('Invalid builtin model catalogue')
    html = (plugin / 'ui/index.html').read_text(encoding='utf-8')
    duration = re.search(r'<select id="duration"[^>]*>(.*?)</select>', html, re.S).group(1)
    if re.findall(r'<option value="([^"]+)"', duration) != ['15', '30']:
        raise ValueError('Duration options must be exactly 15 and 30')
    if 'value="30" selected' not in duration or 'id="use_scene_duration"' in html:
        raise ValueError('Invalid duration defaults')
    if 'v' + version not in html:
        raise ValueError('UI version differs from info.json')

    # Fixed timestamps, path order, file modes and compression make local and CI
    # packages byte-identical. Only this allowlist enters the distributable.
    with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in ('', 'ui/'):
            info = zipfile.ZipInfo(folder + '/' + name, (2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o40755 << 16) | 0x10
            archive.writestr(info, b'')
        for name in FILES:
            info = zipfile.ZipInfo(folder + '/' + name, (2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (plugin / name).read_bytes(), compresslevel=9)
    with zipfile.ZipFile(package) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP integrity check failed')
        actual = [item.filename.removeprefix(folder + '/') for item in archive.infolist() if not item.is_dir()]
        if actual != list(FILES):
            raise ValueError('ZIP file list differs from allowlist')
        for name in FILES:
            if archive.read(folder + '/' + name) != (plugin / name).read_bytes():
                raise ValueError('ZIP content mismatch: ' + name)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    notes = (ROOT / 'RELEASE_NOTES.md').read_text(encoding='utf-8').strip()
    manifest = {
        'plugin': PLUGIN_ID,
        'version': version,
        'download_url': f'https://github.com/{REPOSITORY}/releases/download/v{version}/{filename}',
        'sha256': digest,
        'size': package.stat().st_size,
        'folder': folder,
        'notes': notes,
    }
    (out / 'latest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'SHA256SUMS').write_text(f'{digest}  {filename}\n', encoding='utf-8')
    print(json.dumps({'version': version, 'file': str(package), 'bytes': package.stat().st_size,
                      'sha256': digest, 'files': len(FILES)}, ensure_ascii=False, indent=2))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output')
    args = parser.parse_args()
    build(args.output)
