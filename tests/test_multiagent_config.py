"""Tests for multi-agent environment configuration."""

from __future__ import annotations

import unittest

from godel_forest.multiagent.config import load_multiagent_config


class TestMultiAgentConfig(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        cfg = load_multiagent_config({}, default_model="claude-test")
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.agent_ids, ())
        self.assertEqual(cfg.model, "claude-test")

    def test_mode_multi_uses_default_agents(self) -> None:
        cfg = load_multiagent_config({"GODEL_FOREST_MODE": "multi"}, default_model="m")
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.agent_ids, ("agent001", "agent002", "agent003"))
        self.assertEqual(cfg.agent_error_grace_sec, 300)

    def test_unknown_mode_does_not_enable_multi_agent(self) -> None:
        cfg = load_multiagent_config(
            {"GODEL_FOREST_MODE": "unknown"},
            default_model="m",
        )
        self.assertFalse(cfg.enabled)

    def test_error_grace_can_be_configured(self) -> None:
        cfg = load_multiagent_config(
            {
                "GODEL_FOREST_MODE": "multi",
                "GODEL_FOREST_AGENT_ERROR_GRACE_SEC": "17",
            },
            default_model="m",
        )
        self.assertEqual(cfg.agent_error_grace_sec, 17)

    def test_agent_ids_enable_mode(self) -> None:
        cfg = load_multiagent_config(
            {
                "GODEL_FOREST_AGENT_IDS": "alpha,beta",
                "GODEL_FOREST_AGENT_MODEL": "claude-sonnet-5",
            },
            default_model="fallback",
        )
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.agent_ids, ("alpha", "beta"))
        self.assertEqual(cfg.model, "claude-sonnet-5")

    def test_count_extends_default_agents(self) -> None:
        cfg = load_multiagent_config({"GODEL_FOREST_NUM_AGENTS": "5"}, default_model="m")
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.agent_ids, ("agent001", "agent002", "agent003", "agent004", "agent005"))

    def test_duplicate_agent_ids_raise(self) -> None:
        with self.assertRaises(ValueError):
            load_multiagent_config({"GODEL_FOREST_AGENT_IDS": "a,a"}, default_model="m")

    def test_invalid_agent_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            load_multiagent_config({"GODEL_FOREST_AGENT_IDS": "bad/id"}, default_model="m")


if __name__ == "__main__":
    unittest.main()
