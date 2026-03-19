"""Tests for pending plates + OCR active learning."""
import pytest
import time
import os
import numpy as np
from unittest.mock import MagicMock, patch, call


class TestHandleUnknownPlate:
    """Tests for _handle_unknown_plate in detection_controller."""

    def setup_method(self):
        """Reset cooldown caches before each test."""
        import streamlit_app.controllers.detection_controller as dc
        dc._pending_plate_seen.clear()
        dc._ocr_sample_seen.clear()

    @patch('streamlit_app.controllers.detection_controller._is_plate_whitelisted', return_value=False)
    @patch('streamlit_app.controllers.detection_controller.DatabaseManager')
    def test_unknown_plate_adds_to_pending(self, MockDB, mock_wl):
        from streamlit_app.controllers.detection_controller import _handle_unknown_plate
        mock_db = MockDB.return_value
        _handle_unknown_plate("30B99999", 0.9, None)
        mock_db.add_pending_plate_simple.assert_called_once()
        args = mock_db.add_pending_plate_simple.call_args[0]
        assert args[0] == "30B99999"

    @patch('streamlit_app.controllers.detection_controller._is_plate_whitelisted', return_value=False)
    @patch('streamlit_app.controllers.detection_controller.DatabaseManager')
    def test_cooldown_prevents_duplicate(self, MockDB, mock_wl):
        from streamlit_app.controllers.detection_controller import _handle_unknown_plate
        mock_db = MockDB.return_value
        _handle_unknown_plate("30B99999", 0.9, None)
        _handle_unknown_plate("30B99999", 0.9, None)
        assert mock_db.add_pending_plate_simple.call_count == 1

    @patch('streamlit_app.controllers.detection_controller._is_plate_whitelisted', return_value=True)
    @patch('streamlit_app.controllers.detection_controller.DatabaseManager')
    def test_whitelisted_plate_not_added(self, MockDB, mock_wl):
        from streamlit_app.controllers.detection_controller import _handle_unknown_plate
        mock_db = MockDB.return_value
        _handle_unknown_plate("29A12345", 0.9, None)
        mock_db.add_pending_plate_simple.assert_not_called()


class TestOCRActiveLearning:
    """Tests for OCR sample saving in detection_controller."""

    def setup_method(self):
        import streamlit_app.controllers.detection_controller as dc
        dc._pending_plate_seen.clear()
        dc._ocr_sample_seen.clear()

    @patch('streamlit_app.controllers.detection_controller.cv2')
    @patch('streamlit_app.controllers.detection_controller._is_plate_whitelisted', return_value=False)
    @patch('streamlit_app.controllers.detection_controller.DatabaseManager')
    def test_low_confidence_saves_sample(self, MockDB, mock_wl, mock_cv2):
        from streamlit_app.controllers.detection_controller import _handle_unknown_plate
        mock_db = MockDB.return_value
        crop = np.zeros((50, 100, 3), dtype=np.uint8)
        _handle_unknown_plate("30X11111", 0.5, crop)
        mock_db.save_ocr_training_sample.assert_called_once()

    @patch('streamlit_app.controllers.detection_controller._is_plate_whitelisted', return_value=False)
    @patch('streamlit_app.controllers.detection_controller.DatabaseManager')
    def test_high_confidence_skips_sample(self, MockDB, mock_wl):
        from streamlit_app.controllers.detection_controller import _handle_unknown_plate
        mock_db = MockDB.return_value
        crop = np.zeros((50, 100, 3), dtype=np.uint8)
        _handle_unknown_plate("30X22222", 0.85, crop)
        mock_db.save_ocr_training_sample.assert_not_called()

    @patch('streamlit_app.controllers.detection_controller._is_plate_whitelisted', return_value=False)
    @patch('streamlit_app.controllers.detection_controller.DatabaseManager')
    def test_below_threshold_skips_sample(self, MockDB, mock_wl):
        from streamlit_app.controllers.detection_controller import _handle_unknown_plate
        mock_db = MockDB.return_value
        crop = np.zeros((50, 100, 3), dtype=np.uint8)
        _handle_unknown_plate("30X33333", 0.1, crop)
        mock_db.save_ocr_training_sample.assert_not_called()

    @patch('streamlit_app.controllers.detection_controller.cv2')
    @patch('streamlit_app.controllers.detection_controller._is_plate_whitelisted', return_value=False)
    @patch('streamlit_app.controllers.detection_controller.DatabaseManager')
    def test_ocr_sample_cooldown_120s(self, MockDB, mock_wl, mock_cv2):
        from streamlit_app.controllers.detection_controller import _handle_unknown_plate
        mock_db = MockDB.return_value
        crop = np.zeros((50, 100, 3), dtype=np.uint8)
        _handle_unknown_plate("30X44444", 0.5, crop)
        _handle_unknown_plate("30X44444", 0.5, crop)
        assert mock_db.save_ocr_training_sample.call_count == 1


class TestOCRDatabaseMethods:
    """Tests for OCR training sample DB methods."""

    def _make_db(self):
        from core.database import DatabaseManager
        db = DatabaseManager.__new__(DatabaseManager)
        db._pool = None
        return db

    def test_save_ocr_training_sample(self):
        db = self._make_db()
        with patch.object(db, '_execute_query') as mock_eq:
            db.save_ocr_training_sample("/path/img.jpg", "29A12345", 0.55, "cam1")
        mock_eq.assert_called_once()
        sql = mock_eq.call_args[0][0]
        assert 'INSERT INTO ocr_training_samples' in sql

    def test_review_ocr_sample_approved(self):
        db = self._make_db()
        with patch.object(db, '_execute_query') as mock_eq:
            db.review_ocr_sample(42, "29A12345", "admin", "approved")
        sql = mock_eq.call_args[0][0]
        params = mock_eq.call_args[0][1]
        assert 'UPDATE ocr_training_samples' in sql
        assert params == ("29A12345", "admin", "approved", 42)

    def test_review_ocr_sample_rejected(self):
        db = self._make_db()
        with patch.object(db, '_execute_query') as mock_eq:
            db.review_ocr_sample(7, "", "admin", "rejected")
        params = mock_eq.call_args[0][1]
        assert params[2] == "rejected"

    def test_delete_ocr_sample(self):
        db = self._make_db()
        with patch.object(db, '_execute_query') as mock_eq:
            db.delete_ocr_sample(7)
        sql = mock_eq.call_args[0][0]
        assert 'DELETE FROM ocr_training_samples' in sql
        assert mock_eq.call_args[0][1] == (7,)

    def test_get_ocr_samples_filter_by_status(self):
        db = self._make_db()
        with patch.object(db, '_execute_query', return_value=[]) as mock_eq:
            db.get_ocr_training_samples(status="pending")
        sql = mock_eq.call_args[0][0]
        assert 'WHERE status' in sql

    def test_get_ocr_samples_no_filter(self):
        db = self._make_db()
        with patch.object(db, '_execute_query', return_value=[]) as mock_eq:
            db.get_ocr_training_samples()
        sql = mock_eq.call_args[0][0]
        assert 'WHERE status' not in sql


class TestPendingPlateDB:
    """Tests for add_pending_plate_simple DB method."""

    def _make_db(self):
        from core.database import DatabaseManager
        db = DatabaseManager.__new__(DatabaseManager)
        db._pool = None
        return db

    def test_inserts_row(self):
        db = self._make_db()
        with patch.object(db, '_get_connection') as mc:
            conn = MagicMock()
            cur = MagicMock()
            conn.cursor.return_value.__enter__ = lambda s: cur
            conn.cursor.return_value.__exit__ = lambda *a: None
            mc.return_value.__enter__ = lambda s: conn
            mc.return_value.__exit__ = lambda *a: None
            db.add_pending_plate_simple("29A12345", "29A 12345", "cam1")
        cur.execute.assert_called_once()
        sql = cur.execute.call_args[0][0]
        assert 'INSERT INTO pending_plates' in sql
        assert 'ON CONFLICT' in sql
