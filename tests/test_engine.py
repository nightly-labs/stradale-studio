import unittest
import cv2
import numpy as np
from studio.engine import render

class RenderTests(unittest.TestCase):
    def setUp(self):
        self.image = np.random.default_rng(7).integers(0, 256, (200, 300, 3), dtype=np.uint8)
        self.corners = [[30, 50], [180, 55], [175, 90], [28, 85]]

    def test_only_selected_pixels_change(self):
        result = render(self.image, self.corners, 'STRADALE')
        mask = np.zeros(self.image.shape[:2], np.uint8)
        cv2.fillConvexPoly(mask, np.array(self.corners), 255)
        np.testing.assert_array_equal(result[mask == 0], self.image[mask == 0])
        self.assertTrue(np.any(result[mask != 0] != self.image[mask != 0]))
        self.assertEqual(result.shape, self.image.shape)

    def test_reject_invalid_selection(self):
        for corners in ([[0,0]]*4, [[-1,0],[50,0],[50,30],[0,30]],
                        [[0,0],[50,30],[50,0],[0,30]], [[float('nan'),0]]*4):
            with self.assertRaises(ValueError):
                render(self.image, corners, 'STRADALE')

    def test_reject_empty_or_unsupported_text(self):
        for text in ('', ' ', 'a'*17, '🚙'):
            with self.assertRaises(ValueError):
                render(self.image, self.corners, text)

if __name__ == '__main__':
    unittest.main()
