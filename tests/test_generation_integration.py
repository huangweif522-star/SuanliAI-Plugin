"""Offline regression of generation behavior while plugin updates are added."""
import importlib.util
import tempfile
import types
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests


PLUGIN = Path(__file__).resolve().parents[1] / 'plugin'
spec = importlib.util.spec_from_file_location('suanli_generation_integration', PLUGIN / 'main.py')
main = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'plugin_utils': types.SimpleNamespace(load_plugin_config=lambda _: {})}):
    spec.loader.exec_module(main)

FIXTURE_KEY = 'offline-generation-fixture-not-a-real-token'
FIRST = 'https://fixture.invalid/first.png'
SECOND = 'https://fixture.invalid/second.png'
LAST = 'https://fixture.invalid/last.png'
RAW_MODEL = '007系列/sd2.5(30图10音/4-30秒/720P)'


class Response:
    def __init__(self, payload=None, video=False):
        self.payload = payload or {}
        self.ok = True
        self.status_code = 200
        self.headers = {'Content-Type': 'video/mp4' if video else 'application/json'}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def json(self):
        return self.payload

    def iter_content(self, _):
        yield b'offline-only-video-content'


class GenerationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(requests.Session, 'request', side_effect=AssertionError('Real HTTP is forbidden'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def params(self, saved=None):
        with patch.object(main, 'load_plugin_config', return_value=dict(saved or {})):
            return main.get_params()

    def generate(self, overrides=None, context=None, task_field='id'):
        overrides = dict(overrides or {})
        overrides.update(api_key=FIXTURE_KEY, model=RAW_MODEL)
        with tempfile.TemporaryDirectory() as temporary:
            values = {'prompt': 'offline integration', 'plugin_params': overrides,
                      'reference_images': [FIRST, SECOND], 'output_dir': temporary, 'viewer_index': 2}
            values.update(context or {})
            with patch.object(main, 'load_plugin_config', return_value={}), \
                 patch.object(requests, 'post', return_value=Response({task_field: 'task/with space'})) as post, \
                 patch.object(requests, 'get', side_effect=[Response({'status': 'completed'}), Response(video=True)]) as get:
                result = main.generate(values)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(get.call_count, 2)
            self.assertEqual(len(result), 1)
            output = Path(result[0])
            self.assertTrue(output.name.startswith('0002_video_'))
            self.assertEqual(output.read_bytes(), b'offline-only-video-content')
            self.assertFalse(output.with_suffix('.mp4.part').exists())
            self.assertIn('task%2Fwith%20space', get.call_args_list[0].args[0])
            payload = post.call_args.kwargs['json']
            self.assertIs(type(payload['seconds']), int)
            return payload

    def test_default_mode_duration_ratio_and_timeout(self):
        values = self.params()
        self.assertEqual((values['generation_mode'], values['duration'], values['aspect_ratio'], values['timeout']),
                         ('多参考图生成视频', 30, '9:16', 3600))
        self.assertIs(values['use_scene_duration'], False)
        self.assertEqual(values['api_key'], '')

    def test_default_generation_preserves_exact_id_and_reference_images(self):
        payload = self.generate()
        self.assertEqual(payload['model'], RAW_MODEL)
        self.assertEqual(payload['seconds'], 30)
        self.assertEqual(payload['aspect_ratio'], '9:16')
        self.assertEqual(payload['images'], [FIRST, SECOND])

    def test_only_fifteen_or_thirty_seconds_survive_configuration(self):
        for value, expected in ((15, 15), ('15', 15), (30, 30), ('30', 30), (5, 30), (10, 30),
                                (15.5, 30), (45, 30), (0, 30), (-1, 30), ('bad', 30),
                                (float('nan'), 30), (float('inf'), 30), (True, 30), (None, 30)):
            with self.subTest(value=value):
                values = self.params({'duration': value})
                self.assertEqual(values['duration'], expected)
                self.assertIs(type(values['duration']), int)
                self.assertEqual(self.generate({'duration': value})['seconds'], expected)

    def test_removed_scene_duration_cannot_override_selected_duration(self):
        for scene in (5, 10, 15.5, 45, float('nan'), None):
            with self.subTest(scene=scene):
                payload = self.generate({'duration': 15, 'use_scene_duration': True}, {'scene_duration': scene})
                self.assertEqual(payload['seconds'], 15)

    def test_existing_explicit_text_mode_still_omits_images(self):
        payload = self.generate({'generation_mode': '文生视频'}, {'reference_images': {}})
        self.assertNotIn('images', payload)

    def test_default_multi_mode_without_images_never_posts(self):
        with patch.object(main, 'load_plugin_config', return_value={}), \
             patch.object(requests, 'post') as post, patch.object(requests, 'get') as get:
            with self.assertRaisesRegex(RuntimeError, '需要参考图片'):
                main.generate({'prompt': 'offline', 'plugin_params': {'api_key': FIXTURE_KEY}})
        post.assert_not_called()
        get.assert_not_called()

    def test_multi_reference_dedup_and_frame_order(self):
        payload = self.generate(context={'first_frame_path': FIRST, 'end_frame_path': LAST,
                                        'reference_images': {'参考图片MAP': {'a': [FIRST, SECOND]}}})
        self.assertEqual(payload['images'], [FIRST, SECOND, LAST])

    def test_compatible_task_id_response_field(self):
        self.assertEqual(self.generate(task_field='task_id')['seconds'], 30)

    def test_post_connection_failure_is_never_retried(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(main, 'load_plugin_config', return_value={}), \
             patch.object(requests, 'post', side_effect=requests.ConnectionError('offline')) as post, \
             patch.object(requests, 'get') as get:
            with self.assertRaisesRegex(RuntimeError, '任务可能已受理'):
                main.generate({'prompt': 'offline', 'plugin_params': {'api_key': FIXTURE_KEY},
                               'reference_images': [FIRST], 'output_dir': temporary})
        self.assertEqual(post.call_count, 1)
        get.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
