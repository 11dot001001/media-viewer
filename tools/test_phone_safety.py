"""Regression checks; all phone operations use an in-memory fake device."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mediaviewer.phone import WpdDevice, PhoneLibrary
from mediaviewer.library import Library
from mediaviewer.phonefind import JournalPhoneFinder
from mediaviewer.journal import Journal
from mediaviewer import wpd


class Connection:
    def __init__(self, mode='success'):
        self.mode = mode
        self.sent = []
        self.hashed = False
        self.rows = {
            'storage': dict(name='Internal Storage', folder=True, size=0),
            'month': dict(name='202603_a', folder=True, size=0),
            'fresh': dict(name='IMG_1.DNG', folder=False, size=30000),
        }

    def children(self, parent):
        if self.sent and self.mode == 'disconnect':
            raise OSError('device disconnected')
        return {'DEVICE': ['storage'], 'storage': ['month'],
                'month': [k for k in self.rows if k not in ('month', 'storage')]}[parent]

    def one_strict(self, oid):
        if self.sent and self.mode == 'property_error':
            raise OSError('cannot read properties')
        return self.rows[oid]

    def properties_strict(self, ids):
        return {oid: self.one_strict(oid) for oid in ids}

    def device_info(self):
        return {'serial': 'different' if self.mode == 'wrong_device' else 'serial1'}

    def identity(self, oid):
        return ('changed' if self.hashed and self.mode == 'identity_changed' else 'stable', 'month')

    def sha1(self, oid, size):
        self.hashed = True
        return 'wrong' if self.mode == 'hash_mismatch' else 'expected'

    def delete(self, ids):
        self.sent.extend(ids)
        if self.mode == 'command_error':
            raise OSError('delete failed')
        if self.mode != 'remains':
            del self.rows[ids[0]]
        if self.mode == 'renumbered':
            self.rows['new-id'] = dict(name='IMG_1.DNG', folder=False, size=30000)
        if self.mode == 'reused':
            self.rows[ids[0]] = dict(name='OTHER.DNG', folder=False, size=30000)


REL = 'Internal Storage/202603_a/IMG_1.DNG'


def device():
    d = WpdDevice.__new__(WpdDevice)
    d._ids = {REL: ('stale-id', 30000, 'old-parent')}
    d._lock = threading.Lock()
    d.device_id = 'fake'
    d.name = 'test phone'
    d.serial = 'serial1'
    d.broken = False
    return d


class PhoneSafetyTests(unittest.TestCase):
    def test_enumeration_hresult_error_is_not_an_empty_directory(self):
        c = wpd.Connection.__new__(wpd.Connection)
        enum = SimpleNamespace(**{'_IEnumPortableDeviceObjectIDs__com_Next': lambda *args: -2147467259})
        c.content = SimpleNamespace(EnumObjects=lambda *args: enum)
        with self.assertRaises(RuntimeError):
            c.children('month')

    def test_strict_property_error_propagates(self):
        c = wpd.Connection.__new__(wpd.Connection)
        c.keys = None
        def fail(*args):
            raise OSError('offline')
        c.props = SimpleNamespace(GetValues=fail)
        with self.assertRaises(OSError):
            c.one_strict('fresh')

    def test_no_hash_means_no_deletion(self):
        c = Connection()
        self.assertIsNotNone(device()._delete_one(c, REL, None))
        self.assertFalse(c.sent)

    def test_identity_change_after_hash_means_no_deletion(self):
        c = Connection('identity_changed')
        self.assertIsNotNone(device()._delete_one(c, REL, 'expected'))
        self.assertFalse(c.sent)

    def test_different_phone_means_no_deletion(self):
        c = Connection('wrong_device')
        with patch.object(wpd, 'Connection', return_value=c):
            self.assertIsNotNone(device().delete([REL], {REL: 'expected'})[REL])
        self.assertFalse(c.sent)

    def test_phone_mode_does_not_delete_unverified_main_or_companions(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = PhoneLibrary.__new__(PhoneLibrary)
            lib.root = Path(tmp)
            lib._sizes = {REL: 30000}
            lib.device = device()
            main = dict(id=1, parent_id=None, path=REL, size=30000, sha1=None, match='cloud', match_ref=None)
            child = dict(main, id=2, parent_id=1, path='Internal Storage/202603_a/IMG_1.MOV')
            c = Connection()
            with patch.object(wpd, 'Connection', return_value=c):
                removed, errors, freed = lib._remove([main, child])
            self.assertEqual(removed, [])
            self.assertEqual(freed, 0)
            self.assertEqual(len(errors), 2)
            self.assertFalse(c.sent)

    def test_confirmation_rejects_changed_selection_before_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'photos'
            root.mkdir()
            lib = Library(root, Path(tmp) / 'data')
            old = lib.trash_selection()
            with lib.connect() as c:
                c.execute("INSERT INTO media(path,kind,size,status) VALUES('new.jpg','image',30000,2)")
            with patch.object(lib, '_remove') as remove:
                with self.assertRaises(RuntimeError):
                    lib.empty_trash(expected_ids=old['ids'], expected_token=old['token'])
                remove.assert_not_called()
            c.close()
            import gc, warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                gc.collect()

    def test_success_uses_fresh_identity(self):
        d, c = device(), Connection()
        self.assertIsNone(d._delete_one(c, REL, 'expected'))
        self.assertEqual(c.sent, ['fresh'])
        self.assertNotIn(REL, d._ids)

    def test_error_or_remaining_file_is_never_success(self):
        for mode in ('disconnect', 'property_error', 'remains', 'renumbered', 'command_error'):
            with self.subTest(mode=mode):
                d, c = device(), Connection(mode)
                self.assertIsNotNone(d._delete_one(c, REL, 'expected'))
                self.assertTrue(d.broken)
                self.assertIn(REL, d._ids)
                self.assertIsNotNone(d._delete_one(c, REL, 'expected'))
                self.assertEqual(len(c.sent), 1)

    def test_reused_object_id_does_not_confuse_confirmation(self):
        self.assertIsNone(device()._delete_one(Connection('reused'), REL, 'expected'))

    def test_hash_mismatch_cannot_delete(self):
        c = Connection('hash_mismatch')
        self.assertIsNotNone(device()._delete_one(c, REL, 'expected'))
        self.assertFalse(c.sent)

    def test_missing_parent_is_not_absence_confirmation(self):
        c = Connection()
        c.rows['month']['name'] = 'different'
        d = device()
        self.assertIsNotNone(d._delete_one(c, REL, 'expected'))
        self.assertFalse(c.sent)

    def test_already_missing_is_not_counted_as_deleted(self):
        c = Connection()
        del c.rows['fresh']
        self.assertIsNotNone(device()._delete_one(c, REL, 'expected'))
        self.assertFalse(c.sent)

    def test_attempts_are_persisted_with_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = device()
            d.journal = Journal(Path(tmp) / 'journal.db')
            with patch.object(wpd, 'Connection', return_value=Connection('disconnect')):
                self.assertIsNotNone(d.delete([REL], {REL: 'expected'})[REL])
            row = d.journal.phone_history()[0]
            self.assertEqual(row['status'], 'unconfirmed')
            self.assertIn('disconnected', row['error'])
            self.assertEqual(row['path'], REL)
            self.assertEqual(row['sha1'], 'expected')

    def test_success_and_unfinished_attempt_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = device()
            d.journal = Journal(Path(tmp) / 'journal.db')
            with patch.object(wpd, 'Connection', return_value=Connection()):
                self.assertIsNone(d.delete([REL], {REL: 'expected'})[REL])
            self.assertEqual(d.journal.phone_history()[0]['status'], 'confirmed')
            d.journal.begin_phone_delete('phone', 'pending-file', 'hash')
            self.assertEqual(d.journal.phone_history()[0]['status'], 'pending')

    def test_connection_failure_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = device()
            d.journal = Journal(Path(tmp) / 'journal.db')
            with patch.object(wpd, 'Connection', side_effect=OSError('offline')):
                self.assertIsNotNone(d.delete([REL])[REL])
            self.assertEqual(d.journal.phone_history()[0]['status'], 'unconfirmed')

    def test_journal_search_filters_source_date_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(Path(tmp) / 'journal.db')
            base = dict(name='2026-03-29 12-00-00.DNG', path='a', source='folder', sha1='expected', size=30000, ts=1774785600, ts_source='exif')
            journal.record([base, base, dict(base, source='other', sha1='other'),
                            dict(base, name='2026-03-30 12-00-00.DNG', sha1='next'), dict(base, sha1=None)])
            lib = SimpleNamespace(journal=journal, title='folder', status={'version': 0})
            finder = JournalPhoneFinder(lib, Path(tmp), '2026-03-29')
            self.assertEqual(len(finder.rows), 1)
            self.assertEqual(finder.skipped, 1)
            self.assertFalse(finder._search_by_date())
            with self.assertRaises(RuntimeError):
                finder.delete_selected([finder.rows[0]['id']])
            finder.status['phase'] = 'error'
            self.assertEqual(finder.snapshot()['items'][0]['state'], 'unchecked')


if __name__ == '__main__':
    unittest.main()
