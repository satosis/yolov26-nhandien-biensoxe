"""Tests for door provider CRUD + Tuya door control."""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import os


# ── Door Provider CRUD ──

class TestDoorProviderCRUD:
    def _make_db(self):
        from core.database import DatabaseManager
        db = DatabaseManager.__new__(DatabaseManager)
        db._pool = None
        return db

    @patch('core.database.DatabaseManager._get_connection')
    def test_get_all_door_providers_returns_list(self, mock_conn):
        from core.database import DatabaseManager
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {'provider_key': 'tuya', 'name': 'Tuya Smart'},
            {'provider_key': 'gpio', 'name': 'GPIO Direct'},
        ]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(
            cursor=lambda **kw: MagicMock(
                __enter__=lambda s: mock_cursor,
                __exit__=lambda *a: None
            )
        )
        mock_conn.return_value.__exit__ = lambda *a: None

        db = self._make_db()
        with patch.object(db, '_get_connection') as mc:
            ctx = MagicMock()
            cur = MagicMock()
            cur.fetchall.return_value = [
                {'provider_key': 'tuya', 'name': 'Tuya Smart'},
                {'provider_key': 'gpio', 'name': 'GPIO Direct'},
            ]
            ctx.__enter__ = lambda s: MagicMock(cursor=lambda **kw: MagicMock(
                __enter__=lambda s: cur, __exit__=lambda *a: None))
            ctx.__exit__ = lambda *a: None
            mc.return_value = ctx
            result = db.get_all_door_providers()
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]['provider_key'] == 'tuya'

    def test_upsert_door_provider_calls_execute(self):
        db = self._make_db()
        with patch.object(db, '_get_connection') as mc:
            conn = MagicMock()
            cur = MagicMock()
            conn.cursor.return_value.__enter__ = lambda s: cur
            conn.cursor.return_value.__exit__ = lambda *a: None
            mc.return_value.__enter__ = lambda s: conn
            mc.return_value.__exit__ = lambda *a: None
            result = db.upsert_door_provider('test', 'Test', 'http')
        assert result is True
        cur.execute.assert_called_once()
        sql = cur.execute.call_args[0][0]
        assert 'INSERT INTO door_providers' in sql

    def test_delete_door_provider(self):
        db = self._make_db()
        with patch.object(db, '_execute_query', return_value=True) as mock_eq:
            result = db.delete_door_provider('test')
        assert result is True
        sql = mock_eq.call_args[0][0]
        assert 'DELETE FROM door_providers' in sql


# ── Tuya Door Control ──

class TestTuyaDoorControl:
    @patch.dict(os.environ, {
        'TUYA_DEVICE_ID': 'fake_id',
        'TUYA_LOCAL_KEY': 'fake_key',
        'TUYA_IP': '192.168.1.100',
    })
    @patch('core.door_controller.tinytuya', create=True)
    def test_tuya_local_toggles_dps(self, mock_tinytuya):
        mock_device = MagicMock()
        mock_device.status.return_value = {'dps': {'1': False}}
        mock_tinytuya.OutletDevice.return_value = mock_device

        with patch('core.door_controller._TINYTUYA_AVAILABLE', True):
            from core.door_controller import DoorController
            ctrl = DoorController()
            ctrl.RELAY_TYPE = 'tuya_local'
            ctrl.control_door('OPEN')

        mock_device.set_value.assert_called()

    @patch.dict(os.environ, {'TUYA_DEVICE_ID': '', 'TUYA_LOCAL_KEY': '', 'TUYA_IP': ''})
    def test_tuya_local_skips_when_missing_env(self):
        with patch('core.door_controller._TINYTUYA_AVAILABLE', True):
            from core.door_controller import DoorController
            ctrl = DoorController()
            ctrl.RELAY_TYPE = 'tuya_local'
            # Should not crash
            ctrl.control_door('OPEN')

    def test_tuya_local_skips_when_tinytuya_unavailable(self):
        with patch('core.door_controller._TINYTUYA_AVAILABLE', False):
            from core.door_controller import DoorController
            ctrl = DoorController()
            ctrl.RELAY_TYPE = 'tuya_local'
            ctrl.control_door('OPEN')  # Should not crash

    @patch('requests.get')
    def test_door_state_open(self, mock_get):
        mock_get.return_value.status_code = 200
        from core.door_controller import DoorController
        ctrl = DoorController()
        ctrl.RELAY_TYPE = 'http'
        ctrl.RELAY_HTTP_URL = 'http://fake'
        ctrl.control_door('OPEN')
        assert ctrl.door_state == 'open'

    @patch('requests.get')
    def test_door_state_closed(self, mock_get):
        mock_get.return_value.status_code = 200
        from core.door_controller import DoorController
        ctrl = DoorController()
        ctrl.RELAY_TYPE = 'http'
        ctrl.RELAY_HTTP_URL = 'http://fake'
        ctrl.control_door('CLOSE')
        assert ctrl.door_state == 'closed'
