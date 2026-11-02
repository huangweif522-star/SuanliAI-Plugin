"""Offline updater tests through real requests.PreparedRequest/Session paths."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import zipfile

import requests

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('test_suanli_updates', ROOT / 'plugin/suanli_updates.py')
updates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updates)


def zip_bytes(version='1.1.5', extra=None):
    raw = io.BytesIO()
    folder = 'Suanli_Ai_v' + version
    files = {'main.py': b'pass\n', 'info.json': json.dumps({'version': version}).encode(),
             'ui/index.html': b'<!doctype html><title>plugin</title>'}
    with zipfile.ZipFile(raw, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, value in files.items():
            archive.writestr(folder + '/' + name, value)
        for name, value in (extra or {}).items():
            archive.writestr(name, value)
    return raw.getvalue()


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.plugin = self.folder / 'plugin'
        self.plugin.mkdir()
        (self.plugin / 'info.json').write_text('{"version":"1.1.4"}', encoding='utf-8')
        self.downloads = self.folder / 'Downloads/SuanliAI-Plugin'
        for name, value in (('PLUGIN_DIR', self.plugin), ('DOWNLOAD_DIR', self.downloads)):
            context = patch.object(updates, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.prepared = []
        self.routes = {}
        def send(session, request, **kwargs):
            self.prepared.append((request, kwargs, session.trust_env))
            if request.url not in self.routes:
                raise AssertionError('Unmocked HTTP is forbidden: ' + request.url)
            value = self.routes[request.url]
            if isinstance(value, Exception):
                raise value
            status, raw, headers = value
            response = requests.Response()
            response.status_code = status
            response.url = request.url
            response.request = request
            response._content = raw
            response._content_consumed = True
            response.headers.update(headers)
            return response
        context = patch.object(requests.Session, 'send', new=send)
        context.start()
        self.addCleanup(context.stop)
        self.fixture()
    def route(self, url, data, status=200, headers=None):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.routes[url] = (status, raw, headers or {})
    def fixture(self, version='1.1.5', raw=None):
        self.raw = raw if raw is not None else zip_bytes(version)
        self.version = version
        self.filename = 'Suanli_Ai_v' + version + '.zip'
        self.release_url = updates.RELEASE_BASE + 'tag/v' + version
        self.download_url = updates.RELEASE_BASE + 'download/v' + version + '/' + self.filename
        self.manifest_url = updates.RELEASE_BASE + 'download/v' + version + '/latest.json'
        self.manifest = {'plugin': 'SuanliAI-Plugin', 'version': version,
                         'download_url': self.download_url,
                         'sha256': hashlib.sha256(self.raw).hexdigest(), 'notes': '新版说明'}
        self.release = {'tag_name': 'v' + version, 'html_url': self.release_url,
                        'draft': False, 'prerelease': False, 'published_at': '2026-11-01T12:00:00Z',
                        'assets': [{'name': 'latest.json', 'browser_download_url': self.manifest_url, 'state': 'uploaded'},
                                   {'name': self.filename, 'browser_download_url': self.download_url,
                                    'state': 'uploaded', 'size': len(self.raw)}]}
        self.sync()
    def sync(self):
        self.route(updates.API_LATEST, self.release)
        self.route(self.manifest_url, self.manifest)
        self.route(self.download_url, self.raw)
    def assert_no_credentials(self):
        for request, kwargs, trust_env in self.prepared:
            self.assertIsNone(request.headers.get('Authorization'))
            self.assertIsNone(request.headers.get('Proxy-Authorization'))
            self.assertIsNone(request.body)
            self.assertFalse(kwargs['allow_redirects']) if 'allow_redirects' in kwargs else None
            self.assertFalse(trust_env)
    def test_newer_check_returns_contract_without_internal_fields(self):
        result = updates.check_plugin_update()
        self.assertTrue(result['ok'], result)
        self.assertEqual((result['current_version'], result['latest_version'], result['has_update']), ('1.1.4', '1.1.5', True))
        self.assertEqual(result['download_url'], self.download_url)
        self.assertEqual(result['release_url'], self.release_url)
        self.assertEqual(result['notes'], '新版说明')
        self.assertEqual(len(self.prepared), 2)
        self.assertFalse(any(key.startswith('_') for key in result))
        self.assert_no_credentials()
    def test_same_and_older_are_not_upgrades(self):
        for version in ('1.1.4', '1.1.3', '1.0.99'):
            with self.subTest(version=version):
                self.fixture(version)
                self.assertFalse(updates.check_plugin_update()['has_update'])
                result = updates.download_plugin_update()
                self.assertTrue(result['ok'])
                self.assertFalse(result['has_update'])
                self.assertNotIn('path', result)
        self.assertFalse(self.downloads.exists())
    def test_numeric_semver_order(self):
        self.fixture('1.1.10')
        self.assertTrue(updates.check_plugin_update()['has_update'])
        self.fixture('1.10.0')
        self.assertTrue(updates.check_plugin_update()['has_update'])
    def test_success_download_does_not_extract(self):
        result = updates.download_plugin_update()
        self.assertTrue(result['ok'], result)
        self.assertEqual(Path(result['path']).read_bytes(), self.raw)
        self.assertEqual(result['filename'], self.filename)
        self.assertEqual(result['version'], '1.1.5')
        self.assertFalse(result['already_downloaded'])
        self.assertEqual([item.name for item in self.downloads.iterdir()], [self.filename])
        self.assert_no_credentials()
    def test_matching_existing_zip_is_reused_after_fresh_check(self):
        first = updates.download_plugin_update()
        self.assertTrue(first['ok'])
        before = len(self.prepared)
        second = updates.download_plugin_update()
        self.assertTrue(second['ok'])
        self.assertTrue(second['already_downloaded'])
        self.assertEqual(len(self.prepared) - before, 2)
    def test_download_always_rechecks_ignoring_any_previous_check(self):
        self.assertTrue(updates.check_plugin_update()['has_update'])
        self.fixture('1.1.6')
        result = updates.download_plugin_update()
        self.assertTrue(result['ok'])
        self.assertEqual(result['version'], '1.1.6')
    def test_bad_hash_keeps_preexisting_file(self):
        self.downloads.mkdir(parents=True)
        target = self.downloads / self.filename
        target.write_bytes(b'existing installer retained')
        self.route(self.download_url, self.raw + b'tampered')
        result = updates.download_plugin_update()
        self.assertFalse(result['ok'])
        self.assertIn('SHA256', result['error'])
        self.assertEqual(target.read_bytes(), b'existing installer retained')
        self.assertEqual(list(self.downloads.glob('*.part')), [])
    def test_failed_refresh_preserves_valid_existing_zip(self):
        self.assertTrue(updates.download_plugin_update()['ok'])
        target = self.downloads / self.filename
        before = target.read_bytes()
        self.manifest['sha256'] = '0' * 64
        self.sync()
        self.routes[self.download_url] = requests.ConnectionError('secret remote diagnostics')
        result = updates.download_plugin_update()
        self.assertFalse(result['ok'])
        self.assertEqual(target.read_bytes(), before)
        self.assertNotIn('secret', result['error'])
    def test_atomic_replace_failure_retains_existing_bytes(self):
        self.downloads.mkdir(parents=True)
        target = self.downloads / self.filename
        target.write_bytes(b'old file')
        with patch.object(updates.os, 'replace', side_effect=PermissionError('denied')):
            result = updates.download_plugin_update()
        self.assertFalse(result['ok'])
        self.assertEqual(target.read_bytes(), b'old file')
        self.assertEqual(list(self.downloads.glob('*.part')), [])
    def test_manifest_cannot_point_to_another_host_or_repository(self):
        for url in ('https://evil.invalid/update.zip',
                    self.download_url.replace('huangweif522-star', 'different-owner'),
                    self.download_url + '?token=secret', self.download_url.replace('https:', 'http:')):
            with self.subTest(url=url):
                self.manifest['download_url'] = url
                self.sync()
                self.assertFalse(updates.check_plugin_update()['ok'])
    def test_manifest_and_release_versions_must_match(self):
        self.manifest['version'] = '9.9.9'
        self.sync()
        self.assertFalse(updates.check_plugin_update()['ok'])
    def test_manifest_plugin_id_must_match(self):
        self.manifest['plugin'] = 'another-plugin'
        self.sync()
        self.assertFalse(updates.check_plugin_update()['ok'])
    def test_manifest_hash_is_required(self):
        for value in (None, '', 'not-a-hash', '0' * 63):
            self.manifest['sha256'] = value
            self.sync()
            self.assertFalse(updates.check_plugin_update()['ok'])
    def test_release_assets_must_be_official_installers(self):
        self.release['assets'][1]['name'] = 'Source code.zip'
        self.sync()
        self.assertFalse(updates.check_plugin_update()['ok'])
    def test_release_url_and_tag_are_checked(self):
        for field, value in (('html_url', 'https://github.com/different/repo/releases/tag/v1.1.5'),
                             ('tag_name', 'v1.1.5-beta'), ('tag_name', '1.1.5'),
                             ('draft', True), ('prerelease', True)):
            self.fixture()
            self.release[field] = value
            self.sync()
            self.assertFalse(updates.check_plugin_update()['ok'])
    def test_api_redirect_is_not_followed(self):
        self.route(updates.API_LATEST, b'', 301, {'Location': 'https://api.github.com/repos/other/repo/releases/latest'})
        self.assertFalse(updates.check_plugin_update()['ok'])
        self.assertEqual(len(self.prepared), 1)
    def test_non_github_redirect_is_not_followed(self):
        for host in ('evil.invalid', 'release-assets.githubusercontent.com.evil.invalid', 'evil.githubusercontent.com'):
            with self.subTest(host=host):
                self.route(self.download_url, b'', 302, {'Location': 'https://' + host + '/secret.zip'})
                result = updates.download_plugin_update()
                self.assertFalse(result['ok'])
                self.assertIn('非 GitHub', result['error'])
    def test_official_release_cdn_redirect_is_allowed_without_credentials(self):
        target = 'https://release-assets.githubusercontent.com/github-production-release-asset/fixture?signature=opaque'
        self.route(self.download_url, b'', 302, {'Location': target})
        self.route(target, self.raw)
        result = updates.download_plugin_update()
        self.assertTrue(result['ok'], result)
        self.assert_no_credentials()
    def test_rate_limit_not_found_and_error_messages_are_friendly(self):
        for status in (403, 429, 404, 500):
            self.route(updates.API_LATEST, b'secret-body', status)
            result = updates.check_plugin_update()
            self.assertFalse(result['ok'])
            self.assertNotIn('secret-body', result['error'])
    def test_network_exception_does_not_leak_diagnostics(self):
        for error in (requests.Timeout('secret-token'), requests.ConnectionError('secret-token')):
            self.routes[updates.API_LATEST] = error
            result = updates.check_plugin_update()
            self.assertFalse(result['ok'])
            self.assertNotIn('secret-token', result['error'])
    def test_malformed_json_and_response_limit(self):
        self.route(updates.API_LATEST, b'<html>error</html>')
        self.assertFalse(updates.check_plugin_update()['ok'])
        self.sync()
        with patch.object(updates, 'MAX_API_BYTES', 10):
            self.assertFalse(updates.check_plugin_update()['ok'])
    def test_asset_size_and_github_digest_are_checked(self):
        self.release['assets'][1]['size'] = updates.MAX_ZIP_BYTES + 1
        self.sync()
        self.assertFalse(updates.check_plugin_update()['ok'])
        self.fixture()
        self.release['assets'][1]['digest'] = 'sha256:' + '0' * 64
        self.sync()
        self.assertFalse(updates.check_plugin_update()['ok'])
    def test_manifest_github_digest_is_checked(self):
        self.release['assets'][0]['digest'] = 'sha256:' + '0' * 64
        self.sync()
        self.assertFalse(updates.check_plugin_update()['ok'])
    def test_zip_path_traversal_and_windows_unsafe_names(self):
        for name in ('../escape.py', '/absolute', 'C:/escape.py', 'Suanli_Ai_v1.1.5/../escape.py',
                     'Suanli_Ai_v1.1.5\\escape.py', 'Suanli_Ai_v1.1.5/AUX.txt',
                     'Suanli_Ai_v1.1.5/a:b', 'Suanli_Ai_v1.1.5/trailing. ', 'Suanli_Ai_v1.1.5/double//file'):
            with self.subTest(name=name):
                raw = zip_bytes(extra={name: b'bad'})
                # Windows zipfile normalizes backslashes while authoring. Put
                # the hostile spelling back in both local and central headers.
                if '\\' in name:
                    raw = raw.replace(name.replace('\\', '/').encode(), name.encode())
                self.fixture(raw=raw)
                self.assertFalse(updates.download_plugin_update()['ok'])
                self.assertFalse((self.folder / 'escape.py').exists())
    def test_zip_top_level_and_internal_version_are_checked(self):
        self.fixture(raw=zip_bytes('1.1.6'))
        self.assertFalse(updates.download_plugin_update()['ok'])
        self.fixture(raw=zip_bytes(extra={'Suanli_Ai_v1.1.5/extra/info.json': b'{}'}))
        self.assertTrue(updates.download_plugin_update()['ok'])
    def test_zip_crc_is_checked_after_valid_sha(self):
        self.fixture(raw=zip_bytes().replace(b'pass\n', b'fail\n', 1))
        result = updates.download_plugin_update()
        self.assertFalse(result['ok'])
        self.assertIn('CRC', result['error'])
    def test_zip_symlink_is_rejected(self):
        raw = io.BytesIO(zip_bytes())
        with zipfile.ZipFile(raw, 'a') as archive:
            link = zipfile.ZipInfo('Suanli_Ai_v1.1.5/link')
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, '../../outside')
        self.fixture(raw=raw.getvalue())
        self.assertFalse(updates.download_plugin_update()['ok'])
    def test_zip_casefold_duplicates_are_rejected(self):
        self.fixture(raw=zip_bytes(extra={'Suanli_Ai_v1.1.5/MAIN.py': b'duplicate'}))
        self.assertFalse(updates.download_plugin_update()['ok'])
    def test_zip_expansion_and_entry_limits(self):
        for field, limit in (('MAX_UNPACKED_BYTES', 5), ('MAX_ENTRY_BYTES', 4), ('MAX_ZIP_ENTRIES', 2)):
            with patch.object(updates, field, limit):
                self.assertFalse(updates.download_plugin_update()['ok'])
    def test_local_version_is_read_from_info_not_frontend(self):
        (self.plugin / 'info.json').write_text('{"version":"1.1.9"}', encoding='utf-8')
        result = updates.check_plugin_update()
        self.assertEqual(result['current_version'], '1.1.9')
        self.assertFalse(result['has_update'])
    def test_main_update_actions_never_load_account_configuration(self):
        account = Mock(side_effect=AssertionError('Account configuration must not be read'))
        with patch.dict(sys.modules, {'plugin_utils': types.SimpleNamespace(load_plugin_config=account)}):
            spec = importlib.util.spec_from_file_location('suanli_updater_main_test', ROOT / 'plugin/main.py')
            main = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(main)
            main._updates.PLUGIN_DIR = self.plugin
            main._updates.DOWNLOAD_DIR = self.downloads
            secret = 'suanli-api-key-must-never-be-sent'
            params = {'params': {'api_key': secret, 'base_url': 'https://private.invalid'}}
            self.assertTrue(main.handle_action('check_plugin_update', params)['ok'])
            self.assertTrue(main.handle_action('download_plugin_update', params)['ok'])
            account.assert_not_called()
            for request, _, _ in self.prepared:
                self.assertNotIn(secret, repr((request.url, request.headers, request.body)))
            self.assertEqual(main._DEFAULTS_REVISION, '1.1.4')
            self.assertIn('1.1.3', main._MIGRATED_DEFAULTS_REVISIONS)
            self.assertEqual(main._allowed_seconds(10), 30)
            self.assertEqual(main._allowed_seconds(15), 15)
        self.assert_no_credentials()


if __name__ == '__main__':
    unittest.main(verbosity=2)
