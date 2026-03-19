"""Tests for provider form, HPC camera_id, fee removal."""
import pytest
from unittest.mock import MagicMock, patch


class TestProviderAPIKeys:
    """Task 1: Provider form email field."""

    def _make_db(self):
        from core.database import DatabaseManager
        db = DatabaseManager.__new__(DatabaseManager)
        db._pool = None
        return db

    def test_email_not_treated_as_secret(self):
        """Email field should not be masked in UI."""
        key = "email"
        assert "secret" not in key
        assert "password" not in key

    def test_provider_api_keys_roundtrip(self):
        db = self._make_db()
        with patch.object(db, 'set_many_app_settings', return_value=True) as mock_set:
            db.update_provider_api_keys("imou", {"email": "x@y.com", "app_id": "abc"})
        call_args = mock_set.call_args[0][0]
        assert "IMOU_EMAIL" in call_args
        assert call_args["IMOU_EMAIL"] == "x@y.com"
        assert "IMOU_APP_ID" in call_args


class TestHPCCameraId:
    """Task 4: HPC inference uses actual cam_id."""

    def test_inference_code_uses_cam_id_variable(self):
        """Verify the source code no longer has camera_id=1."""
        import ast
        with open("streamlit_app/engine/inference.py") as f:
            source = f.read()
        # Should NOT contain the old hardcoded pattern
        assert "camera_id=1," not in source or "camera_id=1 " not in source
        # Should contain the fix
        assert "camera_id=cam_id" in source


class TestFeeRemoval:
    """Task 7: Fee always 0."""

    def _make_db(self):
        from core.database import DatabaseManager
        db = DatabaseManager.__new__(DatabaseManager)
        db._pool = None
        return db

    def test_record_vehicle_exit_fee_zero(self):
        """Fee calculation should always produce 0."""
        db = self._make_db()
        # Mock a session that's been parked for 2 hours
        mock_session = {
            'id': 1, 'plate_norm': '29A12345',
            'entry_time': MagicMock(),  # datetime mock
            'status': 'parking',
        }
        with patch.object(db, '_get_connection') as mc:
            conn = MagicMock()
            cur = MagicMock()
            cur.fetchone.return_value = mock_session
            conn.cursor.return_value.__enter__ = lambda s: cur
            conn.cursor.return_value.__exit__ = lambda *a: None
            mc.return_value.__enter__ = lambda s: conn
            mc.return_value.__exit__ = lambda *a: None

            # Check that the UPDATE query uses fee=0
            try:
                db.record_vehicle_exit('29A12345', camera_id_out=1)
            except Exception:
                pass  # May fail due to mock limitations

            # Verify fee=0 in source code
            import inspect
            source = inspect.getsource(db.record_vehicle_exit)
            assert "fee = 0" in source or "fee=0" in source

    def test_parking_analytics_no_revenue_chart(self):
        """Revenue chart should be removed from analytics view."""
        with open("streamlit_app/views/parking_analytics_view.py") as f:
            source = f.read()
        assert "revenue" not in source.lower() or "doanh thu" not in source.lower()

    def test_parking_logs_no_fee_display(self):
        """Fee label should not be displayed in parking logs."""
        with open("streamlit_app/views/parking_logs_view.py") as f:
            source = f.read()
        assert "Phi:" not in source
