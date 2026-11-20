"""核对原始业务资料之间的引用与记录形式。"""

import unittest

from inspect_samples import read_samples


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.data = read_samples()

    def test_bookings_refer_to_whole_trips(self):
        trips = {trip['id']: trip for trip in self.data['trips']}
        rooms = {room['id']: room for room in self.data['rooms']}
        for row in [*self.data['bookings'], *self.data['accepted_changes']]:
            self.assertIn(row['activity'], trips[row['trip']]['chain'])
            self.assertIn(row['activity'], rooms[row['room']]['activities'])

    def test_maintenance_windows_can_overlap(self):
        candidates = self.data['candidates']
        for row in candidates:
            self.assertGreaterEqual(row['end'] - row['start'], row['min_duration'])
        self.assertTrue(any(left['start'] < right['start'] < left['end'] for left in candidates for right in candidates))

    def test_followup_is_after_the_accepted_substitution(self):
        policy = self.data['review_policy']
        self.assertTrue(any(frame['probability'] is None for frame in self.data['sampling_frame']))
        trip_ids = {row['id'] for row in self.data['trips']}
        for note in self.data['followup']:
            self.assertTrue({note['left'],note['right']} <= trip_ids)
            self.assertLess(policy['initial_cutoff'], note['received_order'])
        self.assertTrue(any(row['kind']=='居民已接受换约' and row['received_order']==policy['initial_cutoff'] for row in self.data['decisions']))
