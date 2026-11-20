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
