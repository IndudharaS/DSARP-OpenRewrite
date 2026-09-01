from __future__ import annotations

import unittest

from ml.model_quality import multilabel_metrics, parse_labels, select_predictions


class ModelQualityTests(unittest.TestCase):
    def test_parser_accepts_scored_and_unscored_labels(self) -> None:
        self.assertEqual(parse_labels("Move Class (0.71) | Extract Method"),
                         ["Move Class", "Extract Method"])

    def test_selection_uses_label_thresholds_and_can_abstain(self) -> None:
        self.assertEqual(
            select_predictions([0.7, 0.4], ["Move Class", "Extract Method"],
                               {"Move Class": 0.8, "Extract Method": 0.5}),
            [],
        )
        selected = select_predictions([0.9, 0.6], ["Move Class", "Extract Method"],
                                      {"Move Class": 0.8, "Extract Method": 0.5}, max_labels=1)
        self.assertEqual(selected[0][0], "Move Class")
        self.assertEqual(len(selected), 1)

    def test_metrics_distinguish_precision_recall_and_abstention(self) -> None:
        result = multilabel_metrics(
            [{"Move Class"}, {"Extract Method"}],
            [["Move Class", "Extract Method"], []],
        )
        self.assertEqual(result["coverage"], 0.5)
        self.assertEqual(result["micro_precision"], 0.5)
        self.assertEqual(result["micro_recall"], 0.5)
        self.assertEqual(result["per_label"]["Extract Method"]["recall"], 0.0)


if __name__ == "__main__":
    unittest.main()
