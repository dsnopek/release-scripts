
from Microbuild import Environment

import unittest

class EnvironmentTest(unittest.TestCase):
    def test_basics(self):
        env = Environment({'a': 1, 'b': 2})
        self.assertEquals(env['a'], 1)
        self.assertEquals(env['b'], 2)

        def unknown():
            return env['c']
        self.assertRaises(KeyError, unknown)

        def assignment():
            env['c'] = 3
        self.assertRaises(TypeError, assignment)

        clone = env.clone({'c': 3})
        self.assertEqual(clone['a'], 1)
        self.assertEqual(clone['b'], 2)
        self.assertEqual(clone['c'], 3)

    def test_alternate_constructors(self):
        env = Environment(a=1, b=2)
        self.assertEquals(env['a'], 1)
        self.assertEquals(env['b'], 2)

        clone = env.clone(c=3)
        self.assertEqual(clone['a'], 1)
        self.assertEqual(clone['b'], 2)
        self.assertEqual(clone['c'], 3)
