"""Synthetic regression fixtures; these are not claimed as real chat samples."""
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch

from wxdb.config import WeChatDBConfig
from wxdb.content import parse_message_content
from wxdb.image import (_message_resource_md5, _select_dat_file, decrypt_dat_file,
                        V1_MAGIC_FULL, V2_MAGIC_FULL, resolve_image, _detect_image_format, wxgf_first_frame)
from wxdb.voice import resolve_voice, voice_format


class XMLTests(unittest.TestCase):
    def test_image_fields_and_raw_body(self):
        xml = '<msg><img md5="' + 'a' * 32 + '" length="1024" cdnthumbwidth="80" cdnthumbheight="60" cdnmidimgurl="remote" /></msg>'
        result = parse_message_content(xml, 3)
        self.assertEqual(result["content_parse_status"], "ok")
        self.assertEqual(result["image_metadata"]["length"], 1024)
        self.assertTrue(result["image_metadata"]["has_cdn_reference"])
        self.assertEqual(result["message_text"], "[图片]")
        self.assertNotIn("message_content", result)

    def test_quote_keeps_reply_and_reference_separate(self):
        xml = ('wxid_sender:\n<msg><appmsg><title>我的回复 &amp; 后续</title><type>57</type>'
               '<refermsg><type>1</type><svrid>9223372036854775807</svrid><displayname>好友</displayname>'
               '<content><![CDATA[原文 <不应解析>]]></content></refermsg></appmsg></msg>')
        result = parse_message_content(xml, (57 << 32) | 49)
        self.assertEqual(result["message_type"], "quote")
        self.assertEqual(result["reply_text"], "我的回复 & 后续")
        self.assertEqual(result["reference"]["content"], "原文 <不应解析>")
        self.assertEqual(result["reference"]["server_id"], "9223372036854775807")
        self.assertEqual(result["content_sender_username"], "wxid_sender")

    def test_quote_does_not_parse_nested_image_as_current(self):
        xml = '<msg><appmsg><type>57</type><refermsg><type>3</type><content>&lt;img /&gt;</content></refermsg></appmsg></msg>'
        result = parse_message_content(xml, 49)
        self.assertNotIn("image_metadata", result)
        self.assertIn("[图片]", result["message_text"])
        self.assertNotIn("<img", result["message_text"])

    def test_voice_metadata_not_transcript(self):
        xml = '<msg><voicemsg voicelength="1250" voiceformat="4" length="320" endflag="1" /></msg>'
        result = parse_message_content(xml, 34)
        self.assertEqual(result["voice"]["duration_ms"], 1250)
        self.assertEqual(result["message_text"], "[语音 1.25 秒]")
        self.assertNotIn("transcript", result)

    def test_bad_or_unknown_xml(self):
        for xml, status in [('<msg>', 'invalid_xml'),
                            ('<!DOCTYPE msg [<!ENTITY x "test">]><msg>&x;</msg>', 'unsafe_xml'),
                            ('<other><voicemsg /></other>', 'unsupported')]:
            self.assertEqual(parse_message_content(xml, 34)["content_parse_status"], status)
        self.assertEqual(parse_message_content('<msg><appmsg><type>6</type></appmsg></msg>', 49)["message_type"], "app")
        self.assertEqual(parse_message_content('<msg><appmsg><type>57</type></appmsg></msg>', 49)["content_parse_status"], "missing_reference")


class ImageTests(unittest.TestCase):
    def test_wxgf_is_container_not_bare_hevc(self):
        self.assertEqual(_detect_image_format(b'wxgf' + b'\0' * 12), 'wxgf')
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'image.wxgf'
            source.write_bytes(b'wxgf-invalid')
            with self.assertRaises(ValueError):
                wxgf_first_frame(source)

    def test_zero_aes_segment(self):
        plain = b'\x89PNG\r\n\x1a\n'
        config = WeChatDBConfig('test', Path('.'), {}, image_xor_key=0)
        with tempfile.TemporaryDirectory() as tmp, patch('wxdb.image.get_config', return_value=config):
            source = Path(tmp) / 'image.dat'
            source.write_bytes(V1_MAGIC_FULL + struct.pack('<II', 0, 0) + b'\0' + plain)
            output, kind = decrypt_dat_file(source, Path(tmp) / 'out.png')
            self.assertEqual(output.read_bytes(), plain)
            self.assertEqual(kind, 'png')

    def test_resource_match_is_scoped_to_chat_and_message(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.executescript('CREATE TABLE ChatName2Id(user_name TEXT);'
                         'INSERT INTO ChatName2Id VALUES ("a"),("b");'
                         'CREATE TABLE MessageResourceInfo(chat_id, message_local_id, message_svr_id,'
                         'message_create_time, message_local_type, packed_info);')
        for chat, digest in ((1, "a" * 32), (2, "b" * 32)):
            packed = b'\x12\x22\x0a\x20' + digest.encode()
            db.execute('INSERT INTO MessageResourceInfo VALUES (?,7,99,100,3,?)', (chat, packed))
        def query(path, sql):
            return [dict(r) for r in db.execute(sql)]
        with patch('wxdb.image.run_query', side_effect=query):
            self.assertEqual(_message_resource_md5(7, 'a', 99, 100), 'a' * 32)
            self.assertEqual(_message_resource_md5(7, 'b', 99, 100), 'b' * 32)
            self.assertIsNone(_message_resource_md5(7))
            self.assertIsNone(_message_resource_md5(7, 'a', 98, 100))

    def test_prefer_original_over_thumbnail(self):
        paths = [Path('abc_t.dat'), Path('abc.dat'), Path('abc_h.dat')]
        self.assertEqual(_select_dat_file(paths, 'abc'), paths[2])
        self.assertEqual(_select_dat_file(paths[:2], 'abc'), paths[1])

    def test_container_rejects_truncation_and_invalid_sizes(self):
        config = WeChatDBConfig('test', Path('.'), {}, image_aes_key='a' * 16)
        with tempfile.TemporaryDirectory() as tmp, patch('wxdb.image.get_config', return_value=config):
            source = Path(tmp) / 'bad.dat'
            for data in (V2_MAGIC_FULL, V2_MAGIC_FULL + struct.pack('<II', 4096, 99) + b'\x01'):
                source.write_bytes(data)
                with self.assertRaises(ValueError):
                    decrypt_dat_file(source, Path(tmp) / 'out.png')
                self.assertFalse((Path(tmp) / 'out.png').exists())

    def test_v1_segment_reconstruction(self):
        # AES routine is independently supplied here; verify header arithmetic and XOR boundaries.
        plain = b'\xff\xd8\xff' + b'p' * 13
        middle, tail, xor = b'middle', b'\xff\xd9', 0x88
        ciphertext = b'x' * 32
        data = V1_MAGIC_FULL + struct.pack('<II', 16, 2) + b'\x01' + ciphertext + middle + bytes(x ^ xor for x in tail)
        config = WeChatDBConfig('test', Path('.'), {}, image_xor_key=xor)
        with tempfile.TemporaryDirectory() as tmp, patch('wxdb.image.get_config', return_value=config), \
             patch('wxdb.image._aes_ecb_decrypt', return_value=plain + b'\x10' * 16):
            source = Path(tmp) / 'source.dat'
            source.write_bytes(data)
            out, kind = decrypt_dat_file(source, Path(tmp) / 'out.jpg')
            self.assertEqual(out.read_bytes(), plain + middle + tail)
            self.assertEqual(kind, 'jpg')
            with self.assertRaises(ValueError):
                decrypt_dat_file(source, source)
            self.assertEqual(source.read_bytes(), data)

    def test_failed_resolution_is_not_cached(self):
        config = WeChatDBConfig('test', Path('.'), {})
        with patch('wxdb.image.get_config', return_value=config), \
             patch('wxdb.image._find_dat_files', return_value=[]) as finder:
            for _ in range(2):
                resolve_image(1, 'test', content_md5='a' * 32)
            self.assertEqual(finder.call_count, 2)


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE Name2Id(user_name TEXT);'
                             'INSERT INTO Name2Id VALUES ("a"),("b");'
                             'CREATE TABLE VoiceInfo(chat_name_id,create_time,local_id,svr_id,voice_data);')
        self.data = b'\x02#!SILK_V3' + b'\x03\x00abc'
        self.db.execute('INSERT INTO VoiceInfo VALUES (1,100,7,99,?)', (self.data,))
        self.db.execute('INSERT INTO VoiceInfo VALUES (2,100,7,99,?)', (b'other',))
        self.config = WeChatDBConfig('test', Path('.'), {'message/media_0.db': {}})
        p = patch('wxdb.voice.get_config', return_value=self.config)
        p.start(); self.addCleanup(p.stop)
        q = patch('wxdb.voice.run_query', side_effect=lambda path, sql: [dict(r) for r in self.db.execute(sql)])
        q.start(); self.addCleanup(q.stop)

    def test_scoped_voice_export_preserves_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = resolve_voice('a', 7, 99, 100, tmp)
            self.assertTrue(r['success'])
            self.assertEqual(Path(r['path']).read_bytes(), self.data)
            self.assertEqual(r['format'], 'wechat_silk')
            self.assertFalse(r['decoded'])
            self.assertEqual(resolve_voice('a', 7, 99, 100, tmp)['path'], r['path'])

    def test_no_wrong_chat_or_time_fallback(self):
        self.assertEqual(resolve_voice('b', 7, 99, 100)['error'], 'unknown_voice_format')
        self.assertEqual(resolve_voice('a', 7, 99, 101)['error'], 'voice_not_found')
        self.assertEqual(resolve_voice('c', 7, 99, 100)['error'], 'voice_not_found')

    def test_ambiguous_and_empty_payload(self):
        self.config.db_keys['message/media_1.db'] = {}
        self.assertEqual(resolve_voice('a', 7, 99, 100)['error'], 'ambiguous_voice')
        self.config.db_keys.pop('message/media_1.db')
        self.db.execute('UPDATE VoiceInfo SET voice_data=NULL WHERE chat_name_id=1')
        self.assertEqual(resolve_voice('a', 7, 99, 100)['error'], 'voice_not_downloaded')

    def test_unconfigured_and_unsupported_schema(self):
        self.db.execute('DROP TABLE VoiceInfo')
        self.assertEqual(resolve_voice('a', 7, 99, 100)['error'], 'unsupported_media_schema')
        self.config.db_keys.clear()
        self.assertEqual(resolve_voice('a', 7, 99, 100)['error'], 'media_database_not_configured')
        self.assertEqual(voice_format(b'#!AMR\n123'), 'amr')


if __name__ == '__main__':
    unittest.main()
