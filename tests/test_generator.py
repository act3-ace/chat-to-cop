"""Tests for the synthetic chat data generator."""

from datetime import datetime, timezone

from chat_to_cop.models.cop_update import UpdateType
from chat_to_cop.models.messages import IRCMessage
from chat_to_cop.testing.generator import generate_messages


class TestGenerateMessages:
    def test_correct_count(self):
        msgs, gt = generate_messages(count=20)
        assert len(msgs) == 20
        assert len(gt) == 20

    def test_all_are_irc_messages(self):
        msgs, _ = generate_messages(count=10)
        for m in msgs:
            assert isinstance(m, IRCMessage)

    def test_noise_ratio(self):
        msgs, gt = generate_messages(count=100, noise_ratio=0.3)
        noise_count = sum(1 for g in gt if g is None)
        # Should be roughly 30% noise (with some random variance)
        assert 15 <= noise_count <= 45

    def test_zero_noise(self):
        msgs, gt = generate_messages(count=20, noise_ratio=0.0)
        noise_count = sum(1 for g in gt if g is None)
        assert noise_count == 0

    def test_all_noise(self):
        msgs, gt = generate_messages(count=20, noise_ratio=1.0)
        noise_count = sum(1 for g in gt if g is None)
        assert noise_count == 20

    def test_ground_truth_has_update_types(self):
        msgs, gt = generate_messages(count=100, noise_ratio=0.0)
        types = {g.update_type for g in gt if g is not None}
        # Should have at least a few different types
        assert len(types) >= 3

    def test_timestamps_increase(self):
        msgs, _ = generate_messages(count=20, rate_per_min=5.0)
        for i in range(1, len(msgs)):
            assert msgs[i].timestamp >= msgs[i - 1].timestamp

    def test_custom_start_time(self):
        start = datetime(2025, 9, 23, 10, 0, 0, tzinfo=timezone.utc)
        msgs, _ = generate_messages(count=5, start_time=start)
        assert msgs[0].timestamp == start

    def test_ground_truth_entities_populated(self):
        """Non-noise ground truth should have at least one entity."""
        _, gt = generate_messages(count=50, noise_ratio=0.0)
        for g in gt:
            if g is not None:
                assert len(g.entities) >= 1

    def test_channels_from_expected_set(self):
        msgs, _ = generate_messages(count=50)
        valid_channels = {"#c2_coord", "#isr_reports", "#fires", "#jprc"}
        for m in msgs:
            assert m.channel in valid_channels

    def test_fuel_messages_have_callsigns(self):
        _, gt = generate_messages(count=200, noise_ratio=0.0)
        fuel_updates = [g for g in gt if g is not None and g.update_type == UpdateType.FUEL]
        for fu in fuel_updates:
            for e in fu.entities:
                assert e.callsign is not None
                assert e.fuel_state is not None

    def test_threat_messages_have_track_numbers(self):
        _, gt = generate_messages(count=200, noise_ratio=0.0)
        threat_updates = [g for g in gt if g is not None and g.update_type == UpdateType.THREAT]
        for tu in threat_updates:
            for e in tu.entities:
                assert e.track_number is not None
                assert e.affiliation == "HOSTILE"
