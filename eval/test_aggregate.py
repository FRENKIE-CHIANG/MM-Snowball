"""Offline regression tests for the paper's metrics and aggregation checks."""
import json
import tempfile
import unittest
from pathlib import Path

from eval.aggregate import aggregate_files, calculate_metrics


def judgments(turns=range(1, 7)):
    # Acc1=3/4, Acc5=2/4 -> VFR=100/3%; only dialogue 1 passes T3-T5 -> SRS=25%.
    correct = {1: {1, 2, 3}, 2: {1, 2}, 3: {1, 2}, 4: {1, 3}, 5: {1, 4}, 6: {1, 2, 3, 4}}
    return [{'sample_id': f'{d}_turn_{t}', 'dialogueID': d, 'turn': t,
             'judgment': 'yes' if d in correct[t] else 'no'} for t in turns for d in range(1, 5)]


class MetricTests(unittest.TestCase):
    def test_full_report_uses_joint_correctness(self):
        result = calculate_metrics(judgments(), 4)
        self.assertAlmostEqual(result['vfr_percent'], 100 / 3)
        self.assertEqual(result['srs_percent'], 25)
        self.assertEqual(result['srs_num_successes'], 1)
        self.assertEqual(result['accuracy_percent_by_turn']['6'], 100)

    def test_required_turns_only(self):
        self.assertEqual(calculate_metrics(judgments([3, 4, 5]), 4)['srs_percent'], 25)
        self.assertIsNone(calculate_metrics(judgments([3, 4, 5]), 4)['vfr_percent'])
        self.assertAlmostEqual(calculate_metrics(judgments([1, 5]), 4)['vfr_percent'], 100 / 3)
        self.assertIsNone(calculate_metrics(judgments([1, 5]), 4)['srs_percent'])

    def test_zero_anchor_and_negative_vfr(self):
        rows = judgments([1, 5])
        for row in rows:
            if row['turn'] == 1:
                row['judgment'] = 'no'
        result = calculate_metrics(rows, 4)
        self.assertIsNone(result['vfr_percent'])
        self.assertIn('zero', result['unavailable_metrics']['vfr_percent'])
        rows[0]['judgment'] = 'yes'
        self.assertEqual(calculate_metrics(rows, 4)['vfr_percent'], -100)

    def test_reject_incomplete_duplicate_failed_or_unaligned_samples(self):
        cases = [judgments()[:-1], judgments() + judgments()[:1]]
        unaligned = judgments()
        unaligned[-1] = {**unaligned[-1], 'dialogueID': 99, 'sample_id': '99_turn_6'}
        cases.append(unaligned)
        failed = judgments()
        failed[0]['judgment'] = None
        cases.append(failed)
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                calculate_metrics(rows, 4)

    def test_metadata_and_file_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for turn in range(1, 7):
                path = Path(directory) / f'{turn}.json'
                path.write_text(json.dumps({'judge_model': 'judge', 'evaluated_model': 'vision',
                    'split': 'test', 'num_samples': 4, 'results': judgments([turn])}))
                paths.append(path)
            self.assertEqual(aggregate_files(paths, 4)['metadata_warnings'], [])
            other = json.loads(paths[-1].read_text())
            other['split'] = 'train'
            paths[-1].write_text(json.dumps(other))
            with self.assertRaisesRegex(ValueError, 'different split'):
                aggregate_files(paths, 4)
            other['split'] = 'test'
            other['results'].pop()
            paths[-1].write_text(json.dumps(other))
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                aggregate_files(paths, 4)


if __name__ == '__main__':
    unittest.main()
