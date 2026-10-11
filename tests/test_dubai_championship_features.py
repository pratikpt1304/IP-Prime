import unittest
import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.dlp_shield import DLPShield, dlp_shield
from voice_engine import (
    is_arabic,
    is_hinglish,
    detect_language_mode,
    check_barge_in_phrase,
    EDGE_NEURAL_VOICES,
)
from mobile_room_server import app


class TestDLPShield(unittest.TestCase):
    """Tests for the UAE Zero-Leakage Data Loss Prevention & PII Redaction Engine."""

    def setUp(self):
        self.shield = DLPShield(enabled=True)

    def test_emirates_id_redaction_and_rehydration(self):
        raw = "Please register Emirates ID 784-1995-1234567-1 for the Dubai Chamber portal."
        sanitized, detections = self.shield.sanitize_text(raw)
        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["category"], "EMIRATES_ID")
        self.assertNotIn("784-1995-1234567-1", sanitized)
        self.assertIn("<REDACTED:EMIRATES_ID_1>", sanitized)

        # Verify local re-hydration restores exact Emirates ID
        restored = self.shield.rehydrate_text(
            f"Verified ID {detections[0]['placeholder']} successfully."
        )
        self.assertIn("784-1995-1234567-1", restored)

    def test_uae_iban_and_phone_redaction(self):
        raw = "Transfer AED 25,000 to AE07 0331 2345 6789 0123 456 and notify +971 50 123 4567."
        sanitized, detections = self.shield.sanitize_text(raw)
        categories = {d["category"] for d in detections}
        self.assertIn("UAE_IBAN", categories)
        self.assertIn("UAE_PHONE", categories)
        self.assertNotIn("AE07 0331 2345 6789 0123 456", sanitized)
        self.assertNotIn("123 4567", sanitized)

    def test_api_key_leak_prevention(self):
        raw = "Here is my Gemini key AIzaSyD1234567890abcdefABCDEF123456789 and Groq key gsk_1234567890abcdefghijklmnopqrstuv"
        sanitized, detections = self.shield.sanitize_text(raw)
        categories = {d["category"] for d in detections}
        self.assertIn("API_SECRET", categories)
        self.assertEqual(len(detections), 2)
        self.assertNotIn("AIzaSyD1234567890abcdefABCDEF123456789", sanitized)
        self.assertNotIn("gsk_1234567890abcdefghijklmnopqrstuv", sanitized)

    def test_telemetry_tracking(self):
        self.shield.sanitize_text("Emirates ID: 784-2004-7654321-9")
        telemetry = self.shield.get_telemetry()
        self.assertTrue(telemetry["enabled"])
        self.assertGreaterEqual(telemetry["total_scans"], 1)
        self.assertGreaterEqual(telemetry["total_redactions"], 1)
        self.assertEqual(telemetry["by_category"].get("EMIRATES_ID"), 1)


class TestTrilingualVoiceRouting(unittest.TestCase):
    """Tests for UAE Arabic (ar-AE), Hindi/Hinglish (hi-IN), and English (en-US) voice routing."""

    def test_uae_neural_voices_registered(self):
        self.assertEqual(EDGE_NEURAL_VOICES.get("hamdan"), "ar-AE-HamdanNeural")
        self.assertEqual(EDGE_NEURAL_VOICES.get("fatima"), "ar-AE-FatimaNeural")
        self.assertEqual(EDGE_NEURAL_VOICES.get("arabic"), "ar-AE-HamdanNeural")

    def test_arabic_script_detection(self):
        text = "مرحبا برايم، افتح تقرير المبيعات"
        self.assertTrue(is_arabic(text))
        self.assertEqual(detect_language_mode(text), "ar-AE")

    def test_romanized_emirati_arabic_detection(self):
        text = "Yalla habibi check the invoice shukran"
        self.assertTrue(is_arabic(text))
        self.assertEqual(detect_language_mode(text), "ar-AE")

    def test_hinglish_and_english_detection(self):
        hinglish_text = "Aur bhai kya haal hai, file open karo"
        self.assertTrue(is_hinglish(hinglish_text))
        self.assertEqual(detect_language_mode(hinglish_text), "hi-IN")

        english_text = "Open the quarterly financial dashboard and export to PDF"
        self.assertEqual(detect_language_mode(english_text), "en-US")

    def test_arabic_barge_in_keywords(self):
        self.assertTrue(check_barge_in_phrase("khalas stop now"))
        self.assertTrue(check_barge_in_phrase("خلاص وقف"))


class TestChampionshipFlaskEndpoints(unittest.TestCase):
    """Tests for the 2D Office Floor DLP and Championship Preset API endpoints."""

    def setUp(self):
        self.client = app.test_client()

    def test_dlp_status_and_scan_endpoints(self):
        status_resp = self.client.get("/api/dlp/status")
        self.assertEqual(status_resp.status_code, 200)
        status_data = status_resp.get_json()
        self.assertTrue(status_data["ok"])
        self.assertIn("dlp", status_data)

        scan_resp = self.client.post(
            "/api/dlp/scan",
            json={"text": "Emirates ID 784-1999-1112223-4 and IBAN AE070331234567890123456"},
        )
        self.assertEqual(scan_resp.status_code, 200)
        scan_data = scan_resp.get_json()
        self.assertTrue(scan_data["ok"])
        self.assertGreaterEqual(len(scan_data["detections"]), 2)
        self.assertNotIn("784-1999-1112223-4", scan_data["sanitized"])

    def test_championship_workos_and_access_presets(self):
        for mode in ("workos", "access"):
            resp = self.client.post("/api/championship/preset", json={"mode": mode})
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data["ok"])
            self.assertEqual(data["mode"], mode)
            self.assertEqual(data["spawned_count"], 6)

        # Verify office state includes spawned agents and DLP telemetry
        state_resp = self.client.get("/api/office/state")
        self.assertEqual(state_resp.status_code, 200)
        state_data = state_resp.get_json()
        self.assertIn("dlp", state_data)
        self.assertGreaterEqual(len(state_data["dots"]), 6)


if __name__ == "__main__":
    unittest.main()
