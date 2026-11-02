"""Verify actual outgoing requests without issuing generation or using user keys."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import requests
sys.modules['plugin_utils'] = types.SimpleNamespace(load_plugin_config=lambda _: {})
ROOT = Path(__file__).resolve().parent.parent / 'plugin'
spec = importlib.util.spec_from_file_location('suanli_113_test', ROOT / 'main.py')
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)
KEY = 'offline-fixture-not-a-real-token'
FIRST = 'https://fixture.invalid/first.png'
SECOND = 'https://fixture.invalid/second.png'


class Response:
    ok = True
    status_code = 200
    def __init__(self, body=None, video=False):
        self.body = body or {}
        self.headers = {'Content-Type': 'video/mp4' if video else 'application/json'}
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def json(self):
        return self.body
    def iter_content(self, _):
        yield b'offline-video'


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.block_network = patch.object(requests.Session, 'request', side_effect=AssertionError('Real HTTP forbidden'))
        self.block_network.start()
        self.addCleanup(self.block_network.stop)

    def run_submission(self, saved=None, supplied=None, context_extra=None, job=None):
        params = {'api_key': KEY}
        params.update(supplied or {})
        with tempfile.TemporaryDirectory() as temp:
            context = {'prompt': 'offline test', 'reference_images': [FIRST, SECOND],
                       'output_dir': temp, 'plugin_params': params}
            context.update(context_extra or {})
            with patch.object(main, 'load_plugin_config', return_value=dict(saved or {})), \
                 patch.object(requests, 'post', return_value=Response(job if job is not None else {'id': 'test/with space'})) as post, \
                 patch.object(requests, 'get', side_effect=[Response({'status': 'completed'}), Response(video=True)]) as get:
                outputs = main.generate(context)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(get.call_count, 2)
            self.assertEqual(post.call_args.args[0], 'https://suanliai.top/v1/videos')
            self.assertEqual(Path(outputs[0]).read_bytes(), b'offline-video')
            self.assertFalse(Path(outputs[0]).with_suffix('.mp4.part').exists())
            payload = post.call_args.kwargs['json']
            self.assertIs(type(payload['seconds']), int)
            self.assertIn(payload['seconds'], (15, 30))
            self.assertNotIn('duration', payload)
            self.assertNotIn('use_scene_duration', payload)
            return payload, get.call_args_list

    def test_defaults_submit_30_even_when_scene_duration_is_5(self):
        payload, _ = self.run_submission(context_extra={'scene_duration': 5})
        self.assertEqual(payload['seconds'], 30)
        self.assertEqual(payload['images'], [FIRST, SECOND])
        self.assertEqual((payload['aspect_ratio'], payload['resolution']), ('9:16', '720p'))
        self.assertEqual(main.get_info()['version'], '1.1.4')

    def test_only_15_and_30_are_retained_from_saved_and_task_settings(self):
        for value in (15, '15', '15.0', 30, '30', '30.0'):
            for source in ('saved', 'snapshot'):
                with self.subTest(value=value, source=source):
                    kwargs = {'saved' if source == 'saved' else 'supplied': {'duration': value}}
                    payload, _ = self.run_submission(**kwargs)
                    self.assertEqual(payload['seconds'], int(float(value)))

    def test_obsolete_values_submit_default_30_instead_of_any_other_seconds(self):
        for value in (1, 5, '5', 10, 14.5, 15.5, 29.9, 45, '45', 0, -1,
                      None, '', 'bad', True, False, [15], {}, float('nan'), float('inf')):
            with self.subTest(value=value):
                payload, _ = self.run_submission(supplied={'duration': value})
                self.assertEqual(payload['seconds'], 30)

    def test_old_scene_flags_cannot_override_the_selected_duration(self):
        for duration in (15, 30):
            for flag in (True, 'true', 'on', 'enabled', 1):
                with self.subTest(duration=duration, flag=flag):
                    payload, _ = self.run_submission(saved={'use_scene_duration': flag},
                        supplied={'duration': duration, 'use_scene_duration': flag}, context_extra={'scene_duration': 5})
                    self.assertEqual(payload['seconds'], duration)

    def test_old_5_second_config_with_enabled_override_is_repaired(self):
        saved = {'duration': 5, 'use_scene_duration': True, '_suanli_defaults_revision': '1.1.2'}
        with patch.object(main, 'load_plugin_config', return_value=saved):
            params = main.get_params()
        self.assertEqual(params['duration'], 30)
        self.assertIs(params['use_scene_duration'], False)
        payload, _ = self.run_submission(saved=saved, context_extra={'scene_duration': 5})
        self.assertEqual(payload['seconds'], 30)

    def test_task_snapshot_selected_duration_overrides_later_disk_settings(self):
        for saved, snapshot in ((15, 30), (30, 15), (5, 15)):
            with self.subTest(saved=saved, snapshot=snapshot):
                payload, _ = self.run_submission(saved={'duration': saved}, supplied={'duration': snapshot})
                self.assertEqual(payload['seconds'], snapshot)

    def test_documented_task_id_response_supports_polling_and_download(self):
        payload, gets = self.run_submission(job={'task_id': 'test/with space'})
        self.assertEqual(payload['seconds'], 30)
        self.assertEqual(gets[0].args[0], 'https://suanliai.top/v1/videos/test%2Fwith%20space')
        self.assertEqual(gets[1].args[0], 'https://suanliai.top/v1/videos/test%2Fwith%20space/content')

    def test_task_id_fallback_accepts_missing_invalid_or_blank_id(self):
        for old_id in (None, '', ' ', 123):
            with self.subTest(old_id=old_id):
                _, gets = self.run_submission(job={'id': old_id, 'task_id': 'documented-id'})
                self.assertIn('/videos/documented-id', gets[0].args[0])

    def test_original_id_is_preserved_when_both_response_fields_exist(self):
        _, gets = self.run_submission(job={'id': 'original', 'task_id': 'secondary'})
        self.assertTrue(gets[0].args[0].endswith('/original'))

    def test_prior_markers_keep_custom_aspect_and_timeout_but_drop_old_seconds(self):
        for revision in ('1.1.1', '1.1.2', '1.1.3', '1.1.4'):
            with patch.object(main, 'load_plugin_config', return_value={
                'duration': 10, 'aspect_ratio': '16:9', 'timeout': 900, '_suanli_defaults_revision': revision}):
                params = main.get_params()
            self.assertEqual((params['duration'], params['aspect_ratio'], params['timeout']), (30, '16:9', 900))

    def test_original_multi_reference_order_and_dedup_remain_intact(self):
        payload, _ = self.run_submission(context_extra={'first_frame_path': FIRST, 'end_frame_path': SECOND,
            'reference_images': {'nested': [SECOND, FIRST]}})
        self.assertEqual(payload['images'], [FIRST, SECOND])

    def test_no_images_in_multi_mode_fails_without_posting(self):
        with patch.object(main, 'load_plugin_config', return_value={}), patch.object(requests, 'post') as post:
            with self.assertRaisesRegex(RuntimeError, '需要参考图片'):
                main.generate({'prompt': 'offline', 'plugin_params': {'api_key': KEY}})
        post.assert_not_called()

    def test_ambiguous_submission_is_never_retried(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(main, 'load_plugin_config', return_value={}), \
             patch.object(requests, 'post', side_effect=requests.ConnectionError('fixture')) as post:
            with self.assertRaisesRegex(RuntimeError, '任务可能已受理'):
                main.generate({'prompt': 'offline', 'plugin_params': {'api_key': KEY},
                               'reference_images': [FIRST], 'output_dir': temp})
        self.assertEqual(post.call_count, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
