import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wechat_cli.cli import run, readable, normalize
from wechat_cli.keys import write_config, discover


class CLITests(unittest.TestCase):
    def invoke(self, mode, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = run(mode, args)
        return code, json.loads(output.getvalue())

    def test_missing_arguments_are_machine_readable(self):
        code, result = self.invoke('records', ['messages'])
        self.assertEqual(code, 1)
        self.assertEqual(result['error']['code'], 'INVALID_ARGUMENT')

    def test_errors_do_not_leak_secrets(self):
        with patch('wechat_cli.cli.dispatch', side_effect=RuntimeError('key=SECRET')):
            code, result = self.invoke('records', ['sessions', '--config', 'test.json'])
        self.assertEqual(code, 1)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_large_identifiers_preserve_precision(self):
        result = normalize({'server_id': 9223372036854775807, 'create_time': 1700000000,
                            'reference': {'server_id': 9223372036854775806}})
        self.assertEqual(result['server_id'], '9223372036854775807')
        self.assertIsInstance(result['create_time'], int)
        self.assertIsInstance(result['reference']['server_id'], str)

    def test_verified_group_prefix_only(self):
        row = {'message_type': 'text', 'session_username': 'test@chatroom', 'sender_username': 'sender',
               'message_text': 'sender:\nhello', 'message_content': 'sender:\nhello'}
        self.assertEqual(readable(row)['message_text'], 'hello')
        self.assertNotIn('message_content', readable(row))
        self.assertIn('message_content', readable(row, True))
        row['session_username'] = 'private'
        self.assertEqual(readable(row)['message_text'], 'sender:\nhello')

    def test_existing_config_requires_explicit_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            write_config(path, {'value': 1})
            with self.assertRaises(FileExistsError):
                write_config(path, {'value': 2})
            self.assertEqual(json.loads(path.read_text())['value'], 1)
            write_config(path, {'value': 2}, force=True)
            self.assertEqual(json.loads(path.read_text())['value'], 2)

    def test_discovery_does_not_choose_account(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ('a', 'b'):
                (Path(directory) / name / 'db_storage').mkdir(parents=True)
            self.assertEqual(len(discover(Path(directory))), 2)

    def test_failed_resource_changes_exit_status(self):
        with patch('wechat_cli.cli.dispatch', return_value=[{'success': False, 'error': 'missing'}]):
            code, result = self.invoke('media', ['images', '--config', 'x', '--session', 's', '--output', 'o'])
        self.assertEqual(code, 1)
        self.assertFalse(result['ok'])

    def test_validation_failures_are_not_success(self):
        report = {'samples': [{'type': 'image', 'parse_status': 'ok', 'pixel_decode': 'failed'}]}
        with patch('wechat_cli.cli.dispatch', return_value=report):
            code, result = self.invoke('media', ['validate', '--config', 'x', '--output', 'o'])
        self.assertEqual(code, 1)
        self.assertFalse(result['ok'])


if __name__ == '__main__':
    unittest.main()
