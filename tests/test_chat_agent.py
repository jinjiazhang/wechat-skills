from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from wxdb.config import WeChatDBConfig
from wechat_cli import agent, sending
from wechat_cli.chat_state import ChatError, database, identity
from wechat_cli.windows_sender import WindowsSender


def message(n, sender='other', text='hello'):
    return {'source_db': 'message/message_0.db', 'local_id': n, 'create_time': 100+n,
            'sender_username': sender, 'message_content': text, 'message_text': text,
            'message_type': 'text', 'session_username': 'chat', 'content_decode_status': 'ok'}


class SendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'state.sqlite'
        self.cfg = WeChatDBConfig('self_test', Path(self.tmp.name), {})
        for name, value in [('load_config', self.cfg), ('target', 'Exact Name')]:
            p = patch('wechat_cli.sending.' + name, return_value=value)
            p.start(); self.addCleanup(p.stop)
        self.before = [message(1, 'self')]

    def send(self, backend, **kwargs):
        return sending.send('config', self.state, 'chat', 'self', 'reply', 'token', backend=backend,
                            clock=lambda: 102, **kwargs)

    def test_dry_run_never_uses_backend(self):
        backend = Mock()
        with patch('wechat_cli.sending.rows_for', return_value=self.before):
            self.assertEqual(self.send(backend)['status'], 'dry_run')
        backend.send.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_confirmed_send_is_idempotent(self):
        backend = Mock()
        after = self.before + [message(2, 'self', 'reply')]
        with patch('wechat_cli.sending.rows_for', side_effect=[self.before, after, after]):
            result = self.send(backend, execute=True)
            again = self.send(backend, execute=True)
        self.assertEqual(result['message_id'], identity(after[-1]))
        self.assertTrue(again['deduplicated'])
        backend.send.assert_called_once()

    def test_ui_error_is_durable_and_not_retried(self):
        backend = Mock(); backend.send.side_effect = RuntimeError('input result unknown')
        with patch('wechat_cli.sending.rows_for', return_value=self.before):
            for _ in range(2):
                with self.assertRaises(ChatError) as caught:
                    self.send(backend, execute=True)
                self.assertEqual(caught.exception.code, 'SEND_UNCERTAIN')
        backend.send.assert_called_once()

    def test_timeout_can_reconcile_without_resending(self):
        backend = Mock()
        with patch('wechat_cli.sending.rows_for', return_value=self.before):
            with self.assertRaises(ChatError):
                self.send(backend, execute=True, timeout=0)
        with patch('wechat_cli.sending.rows_for', return_value=self.before + [message(2, 'self', 'reply')]):
            self.assertEqual(sending.reconcile('config', self.state, 'token')['status'], 'confirmed')
        backend.send.assert_called_once()

    def test_confirmation_requires_new_self_message(self):
        rows = [message(1, 'self', 'reply'), message(2, 'other', 'reply')]
        self.assertFalse(sending.confirmed(rows, {identity(rows[0])}, 'self', 'reply', 100))

    def test_ocr_title_not_substring_or_chat_body(self):
        line = {'text': '文 件 传 输 助 手', 'words': [{'x': 350, 'y': 40, 'height': 20}]}
        self.assertTrue(WindowsSender.title_matches('文件传输助手', 1000, 800, [line]))
        self.assertFalse(WindowsSender.title_matches('传输助手', 1000, 800, [line]))
        line['words'][0]['y'] = 400
        self.assertFalse(WindowsSender.title_matches('文件传输助手', 1000, 800, [line]))


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'state.sqlite'
        self.cfg = WeChatDBConfig('self_test', Path(self.tmp.name), {})
        self.rows = [message(1, 'self')]
        for name, options in [('load_config', {'return_value': self.cfg}), ('target', {'return_value': 'Name'}),
                              ('rows_for', {'side_effect': lambda session: list(self.rows)})]:
            p = patch('wechat_cli.agent.' + name, **options)
            p.start(); self.addCleanup(p.stop)

    def init(self, session='chat'):
        agent.initialize('config', self.state, 'test', session, 'self', 'brief', cooldown=0)

    def test_baseline_and_pending_replay(self):
        self.init()
        self.assertEqual(agent.next_batch(self.state, 'test')['status'], 'idle')
        self.rows.append(message(2))
        first = agent.next_batch(self.state, 'test')
        self.assertEqual(first, agent.next_batch(self.state, 'test'))
        self.assertEqual(len(first['messages']), 1)

    def test_no_reply_to_self_in_normal_chat(self):
        self.init(); self.rows.append(message(2, 'self'))
        self.assertEqual(agent.next_batch(self.state, 'test')['status'], 'idle')

    def test_filehelper_accepts_self_input_but_does_not_loop(self):
        self.init('filehelper'); self.rows.append(message(2, 'self'))
        pending = agent.next_batch(self.state, 'test')
        self.assertEqual(pending['status'], 'needs_reply')
        self.rows.append(message(3, 'self', 'reply'))
        fake_send = Mock(return_value={'status': 'confirmed', 'message_id': identity(self.rows[-1])})
        agent.reply(self.state, 'test', pending['ticket'], 'reply', True, fake_send)
        self.assertEqual(agent.next_batch(self.state, 'test')['status'], 'idle')
        self.assertEqual(agent.status(self.state, 'test')['sends'], 1)

    def test_dry_run_does_not_advance_batch(self):
        self.init(); self.rows.append(message(2))
        pending = agent.next_batch(self.state, 'test')
        agent.reply(self.state, 'test', pending['ticket'], 'reply', False, Mock(return_value={'status': 'dry_run'}))
        self.assertEqual(agent.next_batch(self.state, 'test')['ticket'], pending['ticket'])

    def test_human_reply_blocks_stale_automatic_reply(self):
        self.init(); self.rows.append(message(2))
        pending = agent.next_batch(self.state, 'test')
        self.rows.append(message(3, 'self'))
        backend = Mock()
        with self.assertRaises(ChatError) as caught:
            agent.reply(self.state, 'test', pending['ticket'], 'reply', True, backend)
        self.assertEqual(caught.exception.code, 'HUMAN_REPLIED')
        backend.assert_not_called()

    def test_twenty_message_batch_does_not_drop_tail(self):
        self.init(); self.rows.extend(message(n) for n in range(2, 27))
        pending = agent.next_batch(self.state, 'test')
        self.assertEqual(len(pending['messages']), 20)
        agent.skip(self.state, 'test', pending['ticket'])
        self.assertEqual(len(agent.next_batch(self.state, 'test')['messages']), 5)

    def test_stop_blocks_generation_and_sending(self):
        self.init(); self.rows.append(message(2))
        pending = agent.next_batch(self.state, 'test')
        agent.set_active(self.state, 'test', False)
        self.assertEqual(agent.next_batch(self.state, 'test')['status'], 'stopped')
        with self.assertRaises(ChatError) as caught:
            agent.reply(self.state, 'test', pending['ticket'], 'reply', True, Mock())
        self.assertEqual(caught.exception.code, 'AGENT_STOPPED')
        agent.set_active(self.state, 'test', True)
        self.assertEqual(agent.next_batch(self.state, 'test')['ticket'], pending['ticket'])


if __name__ == '__main__':
    unittest.main()
